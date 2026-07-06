import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import datetime
import re
import requests
import uuid


# ============================================================
# LA DESPENSA COMPRAS - ELEVENTA
# App optimizada para iPhone + pistola Bluetooth tipo teclado
# Flujo: cargar catálogo -> escanear código -> editar cantidad/costo/venta -> exportar Excel Eleventa
# ============================================================

st.set_page_config(
    page_title="La Despensa Compras",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed",
)

ELEVENTA_COLUMNS = [
    "Código",
    "Producto",
    "P. Costo",
    "P. Venta",
    "P. Mayoreo",
    "Departamento",
    "Existencia",
    "Inv. Mínimo",
    "Inv. Máximo",
    "Tipo de Venta",
]

NUMERIC_COLUMNS = ["P. Costo", "P. Venta", "P. Mayoreo", "Existencia", "Inv. Mínimo", "Inv. Máximo"]

# -----------------------------
# ESTILO CELULAR
# -----------------------------
st.markdown(
    """
    <style>
    .block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 760px;}
    div[data-testid="stTextInput"] input {font-size: 24px !important; font-weight: 700;}
    div[data-testid="stNumberInput"] input {font-size: 20px !important;}
    .big-code {
        font-size: 30px; font-weight: 800; text-align: center;
        padding: 14px; border-radius: 14px; background: #111827; color: #fff;
        margin: 8px 0 16px 0;
    }
    .card {
        border: 1px solid #e5e7eb; border-radius: 14px; padding: 14px;
        background: #ffffff; margin-bottom: 12px;
    }
    .metric-ok {color: #15803d; font-weight: 800;}
    .metric-warn {color: #ca8a04; font-weight: 800;}
    .metric-bad {color: #dc2626; font-weight: 800;}
    </style>
    """,
    unsafe_allow_html=True,
)

# -----------------------------
# HELPERS
# -----------------------------
def clean_money(value):
    """Convierte valores tipo $1.200, 1,200.50 o texto a float."""
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace("$", "").replace(" ", "")
    # Si viene formato chileno 1.234,56 => 1234.56
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    else:
        # Si solo tiene puntos y son separadores de miles, los elimina
        if text.count(".") >= 1 and re.fullmatch(r"\d{1,3}(\.\d{3})+", text):
            text = text.replace(".", "")
        text = text.replace(",", ".")
    try:
        return float(text)
    except Exception:
        return 0.0


def to_int_stock(value):
    try:
        return int(round(clean_money(value)))
    except Exception:
        return 0


def money0(value):
    try:
        return f"${float(value):,.0f}".replace(",", ".")
    except Exception:
        return "$0"


def normalize_code(value):
    """Normaliza código para comparar: texto, sin espacios, sin .0, y versión sin ceros iniciales."""
    if pd.isna(value):
        return ""
    text = str(value).strip()
    text = text.replace(" ", "")
    if text.endswith(".0"):
        text = text[:-2]
    # Elimina caracteres típicos de lectura errónea, conserva letras por códigos internos tipo PAQ
    text = re.sub(r"[^0-9A-Za-z]", "", text)
    return text


def code_keys(value):
    c = normalize_code(value)
    keys = {c}
    if c.isdigit():
        keys.add(c.lstrip("0") or "0")
        # Algunos EAN/UPC se guardan sin cero inicial o con cero inicial
        if len(c) == 12:
            keys.add("0" + c)
        if len(c) == 11:
            keys.add("0" + c)
            keys.add("00" + c)
    return {k for k in keys if k}


def calc_margin(cost, price):
    cost = clean_money(cost)
    price = clean_money(price)
    utility = price - cost
    margin = (utility / price * 100) if price > 0 else 0.0
    markup = (utility / cost * 100) if cost > 0 else 0.0
    return utility, margin, markup


def margin_class(margin):
    if margin >= 35:
        return "metric-ok"
    if margin >= 25:
        return "metric-warn"
    return "metric-bad"


def ensure_eleventa_columns(df):
    # Limpia nombres
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in ELEVENTA_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("Faltan columnas en el Excel: " + ", ".join(missing))

    # Mantiene columnas originales requeridas y normaliza números
    df = df[ELEVENTA_COLUMNS].copy()
    df["Código"] = df["Código"].apply(lambda x: normalize_code(x))
    df["Producto"] = df["Producto"].fillna("").astype(str)
    df["Departamento"] = df["Departamento"].fillna("").astype(str)
    df["Tipo de Venta"] = df["Tipo de Venta"].fillna("Unidad").astype(str)

    for col in NUMERIC_COLUMNS:
        df[col] = df[col].apply(clean_money)

    return df


def build_index(df):
    idx = {}
    for i, row in df.iterrows():
        for k in code_keys(row["Código"]):
            if k not in idx:
                idx[k] = i
    return idx


def find_product(code):
    code = normalize_code(code)
    if not code or "catalog_index" not in st.session_state:
        return None
    for k in code_keys(code):
        if k in st.session_state.catalog_index:
            return st.session_state.catalog_index[k]
    return None


def get_current_row_for_code(code):
    """Devuelve fila actual considerando modificaciones ya guardadas."""
    norm = normalize_code(code)
    for k in code_keys(norm):
        if k in st.session_state.updates:
            return st.session_state.updates[k].copy(), True

    found_idx = find_product(norm)
    if found_idx is None:
        return None, False
    return st.session_state.catalog_df.loc[found_idx].copy(), False



# -----------------------------
# SUPABASE PERSISTENCE
# -----------------------------
DRAFT_ID = "compra_actual_la_despensa"

def supabase_config():
    url = st.secrets.get("SUPABASE_URL", "").rstrip("/")
    key = st.secrets.get("SUPABASE_KEY", "")
    table = st.secrets.get("SUPABASE_TABLE", "compras_borradores")
    return url, key, table

def supabase_ready():
    url, key, table = supabase_config()
    return bool(url and key and table)

def supabase_headers(prefer=None):
    _, key, _ = supabase_config()
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if prefer:
        h["Prefer"] = prefer
    return h

def row_to_dict(row):
    if isinstance(row, pd.Series):
        d = row.to_dict()
    elif isinstance(row, dict):
        d = dict(row)
    else:
        d = {}
    out = {}
    for col in ELEVENTA_COLUMNS:
        val = d.get(col, "")
        if col in NUMERIC_COLUMNS:
            if col in ["Existencia", "Inv. Mínimo", "Inv. Máximo"]:
                out[col] = int(round(clean_money(val)))
            else:
                out[col] = float(clean_money(val))
        else:
            out[col] = "" if pd.isna(val) else str(val)
    out["Código"] = normalize_code(out.get("Código", ""))
    return out

def serialize_updates():
    unique = {}
    for _, row in st.session_state.get("updates", {}).items():
        d = row_to_dict(row)
        if d.get("Código"):
            unique[d["Código"]] = d
    return list(unique.values())

def restore_updates(rows):
    updates = {}
    if not isinstance(rows, list):
        return updates
    for d in rows:
        if not isinstance(d, dict):
            continue
        full = {col: d.get(col, "" if col not in NUMERIC_COLUMNS else 0) for col in ELEVENTA_COLUMNS}
        full["Código"] = normalize_code(full["Código"])
        if not full["Código"]:
            continue
        for col in NUMERIC_COLUMNS:
            full[col] = clean_money(full[col])
        s = pd.Series(full)
        for k in code_keys(full["Código"]):
            updates[k] = s.copy()
    return updates

def save_draft_to_supabase(show_error=False):
    """Guarda el borrador completo en Supabase. No detiene la app si falla."""
    if not supabase_ready():
        if show_error:
            st.warning("Supabase no está configurado en Secrets.")
        return False

    url, key, table = supabase_config()
    endpoint = f"{url}/rest/v1/{table}?on_conflict=id"

    payload = {
        "id": DRAFT_ID,
        "nombre": "Compra actual La Despensa",
        "data": {
            "updates": serialize_updates(),
            "last_saved": st.session_state.get("last_saved", ""),
            "last_code": st.session_state.get("last_code", ""),
            "updated_from_app": datetime.now().isoformat(timespec="seconds"),
        },
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }

    try:
        r = requests.post(
            endpoint,
            headers=supabase_headers("resolution=merge-duplicates,return=minimal"),
            json=payload,
            timeout=12,
        )
        ok = r.status_code in (200, 201, 204)
        if not ok and show_error:
            st.error(f"Supabase no guardó. HTTP {r.status_code}: {r.text[:500]}")
        return ok
    except Exception as e:
        if show_error:
            st.error(f"Error conectando a Supabase: {e}")
        return False

def load_draft_from_supabase(show_error=False):
    if not supabase_ready():
        return False
    url, key, table = supabase_config()
    endpoint = f"{url}/rest/v1/{table}?id=eq.{DRAFT_ID}&select=data"
    try:
        r = requests.get(endpoint, headers=supabase_headers(), timeout=12)
        if r.status_code != 200:
            if show_error:
                st.error(f"No pude leer Supabase. HTTP {r.status_code}: {r.text[:500]}")
            return False
        rows = r.json()
        if not rows:
            return False
        data = rows[0].get("data", {}) or {}
        st.session_state.updates = restore_updates(data.get("updates", []))
        st.session_state.last_saved = data.get("last_saved", "")
        st.session_state.last_code = data.get("last_code", "")
        return True
    except Exception as e:
        if show_error:
            st.error(f"Error leyendo Supabase: {e}")
        return False

def clear_draft_supabase(show_error=False):
    st.session_state.updates = {}
    st.session_state.current_code = ""
    st.session_state.last_saved = ""
    st.session_state.last_code = ""
    return save_draft_to_supabase(show_error=show_error)

def save_update(row, original_code=None):
    code = normalize_code(row["Código"])
    if original_code:
        code = normalize_code(original_code)
        row["Código"] = code
    row = pd.Series(row_to_dict(row))
    for k in code_keys(code):
        st.session_state.updates[k] = row.copy()
    st.session_state.last_saved = code
    save_draft_to_supabase(show_error=False)


def export_updates_xlsx():
    # Deja una sola fila por código real, evitando duplicados por variantes con/sin cero inicial
    unique = {}
    for _, row in st.session_state.updates.items():
        real_code = normalize_code(row["Código"])
        unique[real_code] = row

    if not unique:
        out = pd.DataFrame(columns=ELEVENTA_COLUMNS)
    else:
        out = pd.DataFrame(list(unique.values()))[ELEVENTA_COLUMNS]

    # Valores enteros donde corresponde
    for col in ["Existencia", "Inv. Mínimo", "Inv. Máximo"]:
        out[col] = out[col].apply(to_int_stock)

    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        out.to_excel(writer, index=False, sheet_name="Productos")
        ws = writer.sheets["Productos"]
        for cell in ws[1]:
            cell.font = cell.font.copy(bold=True)
        widths = {
            "A": 18, "B": 34, "C": 12, "D": 12, "E": 12,
            "F": 18, "G": 12, "H": 12, "I": 12, "J": 14,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width
    buffer.seek(0)
    return buffer.getvalue(), out


def process_scan_callback():
    code = normalize_code(st.session_state.get("scan_code", ""))
    if code:
        st.session_state.current_code = code
        st.session_state.last_code = code
        st.session_state.screen = "product"

# -----------------------------
# SESSION STATE
# -----------------------------
if "catalog_df" not in st.session_state:
    st.session_state.catalog_df = None
if "catalog_index" not in st.session_state:
    st.session_state.catalog_index = {}
if "updates" not in st.session_state:
    st.session_state.updates = {}
if "current_code" not in st.session_state:
    st.session_state.current_code = ""
if "last_code" not in st.session_state:
    st.session_state.last_code = ""
if "last_saved" not in st.session_state:
    st.session_state.last_saved = ""
if "screen" not in st.session_state:
    st.session_state.screen = "scan"
if "draft_loaded" not in st.session_state:
    st.session_state.draft_loaded = False

if not st.session_state.draft_loaded:
    load_draft_from_supabase(show_error=False)
    st.session_state.draft_loaded = True

# -----------------------------
# HEADER
# -----------------------------
st.title("🛒 La Despensa Compras")
st.caption("Pistola Bluetooth + Excel Eleventa. Actualiza solo lo escaneado.")

with st.expander("☁️ Estado Supabase", expanded=False):
    if supabase_ready():
        st.success("Supabase configurado en Secrets.")
        ctest, cload = st.columns(2)
        with ctest:
            if st.button("Crear fila de prueba en Supabase", use_container_width=True):
                before = len(st.session_state.get("updates", {}))
                ok = save_draft_to_supabase(show_error=True)
                if ok:
                    st.success("Fila creada/actualizada en Supabase. Revisa compras_borradores.")
        with cload:
            if st.button("Recargar borrador desde Supabase", use_container_width=True):
                if load_draft_from_supabase(show_error=True):
                    st.success("Borrador recuperado.")
                    st.rerun()
                else:
                    st.warning("No encontré borrador guardado.")
    else:
        st.warning("Supabase no está configurado. Revisa Secrets: SUPABASE_URL, SUPABASE_KEY y SUPABASE_TABLE.")

# -----------------------------
# CARGA DE CATÁLOGO
# -----------------------------
with st.expander("📂 Catálogo Eleventa", expanded=st.session_state.catalog_df is None):
    uploaded = st.file_uploader("Sube el Excel exportado desde Eleventa", type=["xlsx"])

    if uploaded is not None:
        try:
            raw_df = pd.read_excel(uploaded, dtype={"Código": str})
            st.session_state.catalog_df = ensure_eleventa_columns(raw_df)
            st.session_state.catalog_index = build_index(st.session_state.catalog_df)
            st.success(f"Catálogo cargado: {len(st.session_state.catalog_df):,} productos".replace(",", "."))
        except Exception as e:
            st.error(f"No pude leer el catálogo: {e}")
    elif st.session_state.catalog_df is None:
        st.info("Primero sube el catálogo exportado de Eleventa para poder buscar productos.")

if st.session_state.catalog_df is not None:
    st.success(f"✅ Catálogo activo: {len(st.session_state.catalog_df):,} productos".replace(",", "."))

# -----------------------------
# ZONA DE ESCANEO
# -----------------------------
st.subheader("🔫 Escanear producto")
st.markdown("Toca el cuadro y escanea con la pistola Bluetooth.")

st.text_input(
    "Código de barras",
    key="scan_code",
    placeholder="Escanea aquí...",
    label_visibility="collapsed",
    on_change=process_scan_callback,
)

col_a, col_b = st.columns(2)
with col_a:
    if st.button("🔎 Buscar código", use_container_width=True):
        code = normalize_code(st.session_state.get("scan_code", ""))
        if code:
            st.session_state.current_code = code
            st.session_state.last_code = code
            st.session_state.screen = "product"
            st.rerun()
        else:
            st.warning("Escanea o escribe un código.")
with col_b:
    if st.button("🧹 Limpiar", use_container_width=True):
        st.session_state.current_code = ""
        st.session_state.screen = "scan"
        st.rerun()

if st.session_state.last_code:
    st.markdown(f"<div class='big-code'>Último código: {st.session_state.last_code}</div>", unsafe_allow_html=True)

if st.session_state.last_saved:
    st.success(f"Último guardado: {st.session_state.last_saved}")

# -----------------------------
# PRODUCTO EXISTENTE / NUEVO
# -----------------------------
code = normalize_code(st.session_state.current_code)

if code:
    row, already_modified = get_current_row_for_code(code)

    if row is not None:
        st.subheader("✅ Producto encontrado")
        utilidad_actual, margen_actual, markup_actual = calc_margin(row["P. Costo"], row["P. Venta"])

        st.markdown(
            f"""
            <div class='card'>
                <b>Código:</b> {row['Código']}<br>
                <b>Producto:</b> {row['Producto']}<br>
                <b>Departamento:</b> {row['Departamento']}<br>
                <b>Stock actual:</b> {to_int_stock(row['Existencia'])}<br>
                <b>Costo actual:</b> {money0(row['P. Costo'])}<br>
                <b>Venta actual:</b> {money0(row['P. Venta'])}<br>
                <b>Margen actual:</b> <span class='{margin_class(margen_actual)}'>{margen_actual:.1f}%</span><br>
                <b>Utilidad unitaria:</b> {money0(utilidad_actual)}
            </div>
            """,
            unsafe_allow_html=True,
        )

        if already_modified:
            st.info("Este producto ya fue modificado en esta sesión. La nueva cantidad se sumará sobre el stock ya actualizado.")

        with st.form("form_existing_product", clear_on_submit=False):
            cantidad_comprada = st.number_input("Cantidad comprada", min_value=0.0, value=1.0, step=1.0)
            nuevo_costo = st.number_input("Nuevo precio costo", min_value=0.0, value=float(clean_money(row["P. Costo"])), step=10.0)
            nuevo_venta = st.number_input("Nuevo precio venta", min_value=0.0, value=float(clean_money(row["P. Venta"])), step=10.0)
            nuevo_mayoreo = st.number_input("Nuevo precio mayoreo", min_value=0.0, value=float(clean_money(row["P. Mayoreo"])), step=10.0)

            modo_inv = st.radio(
                "Inventario",
                ["Sumar compra al stock", "Reemplazar stock", "No modificar stock"],
                index=0,
                horizontal=False,
            )

            if modo_inv == "Sumar compra al stock":
                nueva_existencia = to_int_stock(row["Existencia"]) + cantidad_comprada
            elif modo_inv == "Reemplazar stock":
                nueva_existencia = cantidad_comprada
            else:
                nueva_existencia = to_int_stock(row["Existencia"])

            utilidad_nueva, margen_nuevo, markup_nuevo = calc_margin(nuevo_costo, nuevo_venta)

            st.markdown(
                f"""
                <div class='card'>
                    <b>Nuevo stock:</b> {int(round(nueva_existencia))}<br>
                    <b>Nueva utilidad:</b> {money0(utilidad_nueva)}<br>
                    <b>Nuevo margen:</b> <span class='{margin_class(margen_nuevo)}'>{margen_nuevo:.1f}%</span><br>
                    <b>Markup sobre costo:</b> {markup_nuevo:.1f}%
                </div>
                """,
                unsafe_allow_html=True,
            )

            guardar = st.form_submit_button("💾 Guardar producto", use_container_width=True)

        if guardar:
            updated = row.copy()
            updated["P. Costo"] = float(nuevo_costo)
            updated["P. Venta"] = float(nuevo_venta)
            updated["P. Mayoreo"] = float(nuevo_mayoreo)
            updated["Existencia"] = int(round(nueva_existencia))
            save_update(updated, original_code=row["Código"])
            st.session_state.current_code = ""
            st.session_state.screen = "scan"
            st.success("Producto guardado. Escanea el siguiente.")
            st.rerun()

    else:
        st.subheader("➕ Producto no encontrado")
        st.warning(f"Código leído: {code}")

        with st.form("form_new_product", clear_on_submit=False):
            nuevo_codigo = st.text_input("Código", value=code)
            nuevo_producto = st.text_input("Nombre del producto", value="")
            nuevo_departamento = st.text_input("Departamento", value="")
            cantidad_inicial = st.number_input("Cantidad inicial", min_value=0.0, value=1.0, step=1.0)
            costo = st.number_input("Precio costo", min_value=0.0, value=0.0, step=10.0)
            venta = st.number_input("Precio venta", min_value=0.0, value=0.0, step=10.0)
            mayoreo = st.number_input("Precio mayoreo", min_value=0.0, value=0.0, step=10.0)
            inv_min = st.number_input("Inventario mínimo", min_value=0.0, value=0.0, step=1.0)
            inv_max = st.number_input("Inventario máximo", min_value=0.0, value=0.0, step=1.0)
            tipo_venta = st.selectbox("Tipo de Venta", ["Unidad", "Granel"], index=0)

            utilidad, margen, markup = calc_margin(costo, venta)
            st.markdown(
                f"""
                <div class='card'>
                    <b>Utilidad:</b> {money0(utilidad)}<br>
                    <b>Margen:</b> <span class='{margin_class(margen)}'>{margen:.1f}%</span><br>
                    <b>Markup:</b> {markup:.1f}%
                </div>
                """,
                unsafe_allow_html=True,
            )

            crear = st.form_submit_button("➕ Crear producto para importar", use_container_width=True)

        if crear:
            if not normalize_code(nuevo_codigo):
                st.error("El código no puede quedar vacío.")
            elif not nuevo_producto.strip():
                st.error("El producto necesita nombre.")
            else:
                new_row = pd.Series({
                    "Código": normalize_code(nuevo_codigo),
                    "Producto": nuevo_producto.strip(),
                    "P. Costo": float(costo),
                    "P. Venta": float(venta),
                    "P. Mayoreo": float(mayoreo),
                    "Departamento": nuevo_departamento.strip(),
                    "Existencia": int(round(cantidad_inicial)),
                    "Inv. Mínimo": int(round(inv_min)),
                    "Inv. Máximo": int(round(inv_max)),
                    "Tipo de Venta": tipo_venta,
                })
                save_update(new_row, original_code=nuevo_codigo)
                st.session_state.current_code = ""
                st.session_state.screen = "scan"
                st.success("Producto nuevo guardado. Escanea el siguiente.")
                st.rerun()

# -----------------------------
# RESUMEN Y EXPORTACIÓN
# -----------------------------
st.divider()
st.subheader("📦 Productos listos para importar")

if st.session_state.updates:
    _, preview_df = export_updates_xlsx()
    st.dataframe(preview_df, use_container_width=True, hide_index=True)

    xlsx_bytes, final_df = export_updates_xlsx()
    filename = f"importacion_eleventa_la_despensa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
    st.download_button(
        "⬇️ Descargar Excel para Eleventa",
        data=xlsx_bytes,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

    if st.button("🗑️ Borrar lista de productos modificados", use_container_width=True):
        clear_draft_supabase(show_error=True)
        st.rerun()
else:
    st.info("Aún no hay productos guardados para exportar.")

# -----------------------------
# INSTRUCCIONES ELEVENTA
# -----------------------------
with st.expander("ℹ️ Cómo importar en Eleventa"):
    st.markdown(
        """
        1. En Eleventa entra a **F3 Productos → Importar**.  
        2. Selecciona el Excel descargado desde esta app.  
        3. Relaciona las columnas con los campos de Eleventa.  
        4. Marca **Actualizar los productos cuyo código ya exista**.  
        5. Marca **Actualizar el inventario del producto** si quieres aplicar las cantidades.  
        6. Importa y verifica una muestra de productos.

        El archivo exportado contiene **solo los productos escaneados o creados**, no todo el catálogo.
        """
    )
