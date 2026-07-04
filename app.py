import io
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html
from streamlit_js_eval import streamlit_js_eval

st.set_page_config(
    page_title="La Despensa Compras",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .block-container {max-width: 820px; padding-top: 1.2rem; padding-bottom: 5rem;}
    .big-card {padding:16px; border-radius:16px; border:1px solid #e4e4e4; background:#fafafa; margin:10px 0 16px 0;}
    .product-title {font-size:1.35rem; font-weight:900; line-height:1.25;}
    .muted {color:#666; font-size:0.92rem;}
    .metric-card {padding:12px; border-radius:14px; background:#fff; border:1px solid #e8e8e8;}
    button[kind="primary"] {width:100%;}
    div[data-testid="stDownloadButton"] button {width:100%;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛒 La Despensa Compras")
st.caption("Escanea productos con el celular, suma inventario, ajusta costos/precios y genera Excel para Eleventa.")

DEFAULT_ALIASES = {
    "codigo": ["codigo", "código", "codigo de barras", "código de barras", "codigo producto", "código producto", "clave", "barcode", "sku"],
    "descripcion": ["descripcion", "descripción", "producto", "nombre", "articulo", "artículo", "descripcion del producto", "descripción del producto"],
    "costo": ["costo", "precio costo", "precio de costo", "costo unitario", "precio compra", "precio_compra", "cost"],
    "venta": ["precio venta", "precio de venta", "venta", "precio", "precio publico", "precio público", "pventa"],
    "mayoreo": ["precio mayoreo", "precio de mayoreo", "mayoreo", "precio mayorista"],
    "inventario": ["inventario", "existencia", "existencia actual", "cantidad", "hay", "stock"],
    "departamento": ["departamento", "categoria", "categoría", "familia"],
    "tipo_venta": ["tipo de venta", "tipo venta", "se vende", "unidad", "tipo"],
}

FIELDS = ["codigo", "descripcion", "costo", "venta", "mayoreo", "inventario", "departamento", "tipo_venta"]


def normalize_text(x: str) -> str:
    return str(x).strip().lower().replace("_", " ").replace("-", " ")


def guess_column(columns: List[str], key: str) -> Optional[str]:
    normalized = {normalize_text(c): c for c in columns}
    for alias in DEFAULT_ALIASES[key]:
        if alias in normalized:
            return normalized[alias]
    for c in columns:
        nc = normalize_text(c)
        for alias in DEFAULT_ALIASES[key]:
            if alias in nc or nc in alias:
                return c
    return None


def clean_code(code: str) -> str:
    return re.sub(r"[^0-9A-Za-z\-_.]", "", str(code).strip())


def read_excel(uploaded) -> pd.DataFrame:
    return pd.read_excel(uploaded, dtype=str).fillna("")


def to_number(value, default: float = 0.0) -> float:
    try:
        s = str(value).replace("$", "").replace(" ", "").strip()
        if not s:
            return default
        # Chile format: 1.250,5 => 1250.5 ; simple integer 1250 => 1250
        if "," in s and "." in s:
            s = s.replace(".", "").replace(",", ".")
        elif "," in s:
            s = s.replace(",", ".")
        elif s.count(".") > 1:
            s = s.replace(".", "")
        return float(s)
    except Exception:
        return default


def clp(value) -> str:
    try:
        return "$" + f"{int(round(float(value))):,}".replace(",", ".")
    except Exception:
        return "$0"


def pct(value) -> str:
    try:
        return f"{float(value):.1f}%".replace(".", ",")
    except Exception:
        return "0,0%"


def margin_on_sale(cost: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return ((price - cost) / price) * 100


def markup_on_cost(cost: float, price: float) -> float:
    if cost <= 0:
        return 0.0
    return ((price - cost) / cost) * 100


def price_from_margin(cost: float, margin_percent: float) -> float:
    if margin_percent >= 100:
        return cost
    return cost / (1 - margin_percent / 100)


def find_product(df: pd.DataFrame, col_code: str, code: str) -> Optional[Dict]:
    code = clean_code(code)
    if not col_code or not code or df.empty:
        return None
    matches = df[df[col_code].astype(str).map(clean_code) == code]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def original_stock(df: pd.DataFrame, mapping: Dict[str, str], code: str) -> float:
    product = find_product(df, mapping.get("codigo", ""), code)
    if product and mapping.get("inventario"):
        return to_number(product.get(mapping["inventario"], 0), 0)
    return 0.0


def safe_get(product: Optional[Dict], mapping: Dict[str, str], field: str, default=""):
    if not product:
        return default
    col = mapping.get(field)
    if col:
        return product.get(col, default)
    return default


def build_export(df_original: pd.DataFrame, updates: List[Dict], mapping: Dict[str, str], only_updates: bool = True) -> io.BytesIO:
    df = df_original.copy()
    col_code = mapping.get("codigo")
    if not col_code:
        raise ValueError("Falta seleccionar la columna de código de barras.")

    for upd in updates:
        code = clean_code(upd.get("codigo", ""))
        idx = df[df[col_code].astype(str).map(clean_code) == code].index
        is_new = len(idx) == 0

        if is_new:
            row = {c: "" for c in df.columns}
            row[col_code] = code
            for field in FIELDS:
                col = mapping.get(field)
                if col and field in upd:
                    if field == "inventario":
                        row[col] = upd.get("stock_final", upd.get("cantidad_comprada", 0))
                    else:
                        row[col] = upd.get(field, "")
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            continue

        i = idx[0]
        for field in ["descripcion", "costo", "venta", "mayoreo", "departamento", "tipo_venta"]:
            col = mapping.get(field)
            if col and upd.get(f"actualizar_{field}", True) and upd.get(field, "") not in [None, ""]:
                df.at[i, col] = upd[field]

        col_inv = mapping.get("inventario")
        if col_inv and upd.get("actualizar_inventario", True):
            df.at[i, col_inv] = upd.get("stock_final", df.at[i, col_inv])

    if only_updates:
        codes = {clean_code(u.get("codigo", "")) for u in updates}
        df = df[df[col_code].astype(str).map(clean_code).isin(codes)]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Productos")
    output.seek(0)
    return output


def scanner_html() -> None:
    # Html5Qrcode runs in the browser. It stores the last code in localStorage.
    html(
        """
        <div style="font-family:Arial, sans-serif; max-width:560px; margin:auto;">
          <div id="reader" style="width:100%; border-radius:14px; overflow:hidden;"></div>
          <div style="display:flex; gap:8px; margin-top:10px;">
            <button id="startBtn" style="flex:1; padding:13px; border-radius:12px; border:0; background:#111; color:#fff; font-weight:800;">📷 Iniciar cámara</button>
            <button id="stopBtn" style="flex:1; padding:13px; border-radius:12px; border:1px solid #999; background:#fff; color:#111; font-weight:800;">Detener</button>
          </div>
          <div id="status" style="margin-top:10px; color:#555; font-size:14px;">Presiona iniciar cámara y apunta al código de barras.</div>
          <div id="last" style="margin-top:8px; font-size:18px; font-weight:800;"></div>
        </div>
        <script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
        <script>
          let scanner = null;
          let running = false;
          const statusEl = document.getElementById('status');
          const lastEl = document.getElementById('last');

          function saveCode(code) {
            const clean = String(code || '').trim();
            localStorage.setItem('la_despensa_last_barcode', clean);
            localStorage.setItem('la_despensa_last_barcode_time', new Date().toISOString());
            lastEl.innerText = 'Código leído: ' + clean;
            statusEl.innerText = 'Código detectado. Ahora toca en Streamlit: Usar último código escaneado.';
          }

          async function startScanner() {
            try {
              if (running) return;
              scanner = new Html5Qrcode('reader');
              await scanner.start(
                { facingMode: 'environment' },
                { fps: 12, qrbox: { width: 300, height: 170 }, aspectRatio: 1.333 },
                (decodedText) => saveCode(decodedText),
                () => {}
              );
              running = true;
              statusEl.innerText = 'Cámara activa. Apunta al código.';
            } catch (err) {
              statusEl.innerText = 'No se pudo abrir la cámara. Debe estar en HTTPS y debes dar permiso de cámara.';
              console.error(err);
            }
          }

          async function stopScanner() {
            try {
              if (scanner && running) {
                await scanner.stop();
                await scanner.clear();
                running = false;
                statusEl.innerText = 'Cámara detenida.';
              }
            } catch (err) { console.error(err); }
          }

          document.getElementById('startBtn').onclick = startScanner;
          document.getElementById('stopBtn').onclick = stopScanner;
        </script>
        """,
        height=540,
    )


if "updates" not in st.session_state:
    st.session_state.updates = []
if "current_code" not in st.session_state:
    st.session_state.current_code = ""
if "scan_nonce" not in st.session_state:
    st.session_state.scan_nonce = 0

with st.expander("1) Cargar catálogo/exportación de Eleventa", expanded=True):
    uploaded = st.file_uploader("Sube el Excel exportado desde Eleventa", type=["xlsx", "xls"])
    st.caption("La app generará después otro Excel solo con los productos escaneados/modificados.")

if not uploaded:
    st.info("Primero sube el Excel de productos de Eleventa.")
    st.stop()

try:
    df = read_excel(uploaded)
except Exception as exc:
    st.error(f"No pude leer el Excel: {exc}")
    st.stop()

st.success(f"Catálogo cargado: {len(df)} filas")

with st.expander("2) Confirmar columnas detectadas", expanded=False):
    cols = list(df.columns)
    mapping = {}
    labels = {
        "codigo": "Código de barras",
        "descripcion": "Descripción",
        "costo": "Precio costo",
        "venta": "Precio venta",
        "mayoreo": "Precio mayoreo",
        "inventario": "Inventario / existencia",
        "departamento": "Departamento",
        "tipo_venta": "Tipo de venta",
    }
    for key in FIELDS:
        guessed = guess_column(cols, key)
        index = cols.index(guessed) + 1 if guessed in cols else 0
        mapping[key] = st.selectbox(labels[key], [""] + cols, index=index, key=f"map_{key}")

if not mapping.get("codigo"):
    st.error("Selecciona la columna de código de barras.")
    st.stop()

st.subheader("3) Escanear producto")
st.caption("En Streamlit Cloud funciona mejor porque la cámara requiere HTTPS.")
scanner_html()

col_scan, col_manual = st.columns([1, 1])
with col_scan:
    if st.button("✅ Usar último código escaneado", type="primary"):
        st.session_state.scan_nonce += 1
with col_manual:
    if st.button("🧹 Borrar código actual"):
        st.session_state.current_code = ""
        st.rerun()

last_code = streamlit_js_eval(
    js_expressions="localStorage.getItem('la_despensa_last_barcode')",
    key=f"get_barcode_{st.session_state.scan_nonce}",
)
if last_code:
    st.session_state.current_code = clean_code(last_code)

manual = st.text_input("Código manual / escaneado", value=st.session_state.current_code, placeholder="Escanea o escribe el código")
if manual != st.session_state.current_code:
    st.session_state.current_code = clean_code(manual)
    st.rerun()

code = clean_code(st.session_state.current_code)
product = find_product(df, mapping["codigo"], code) if code else None

if code:
    exists = product is not None
    desc_actual = str(safe_get(product, mapping, "descripcion", ""))
    costo_actual = to_number(safe_get(product, mapping, "costo", 0), 0)
    venta_actual = to_number(safe_get(product, mapping, "venta", 0), 0)
    stock_actual = to_number(safe_get(product, mapping, "inventario", 0), 0)
    mayoreo_actual = to_number(safe_get(product, mapping, "mayoreo", 0), 0)
    depto_actual = str(safe_get(product, mapping, "departamento", ""))
    tipo_actual = str(safe_get(product, mapping, "tipo_venta", ""))

    if exists:
        margen_actual = margin_on_sale(costo_actual, venta_actual)
        st.markdown(
            f"""
            <div class="big-card">
              <div class="product-title">{desc_actual or 'Producto encontrado'}</div>
              <div class="muted">Código: {code}</div>
              <br>
              <div>Stock actual: <b>{stock_actual:g}</b></div>
              <div>Costo actual: <b>{clp(costo_actual)}</b></div>
              <div>Venta actual: <b>{clp(venta_actual)}</b></div>
              <div>Margen actual: <b>{pct(margen_actual)}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.warning("Código no encontrado. Se agregará como producto nuevo al Excel de importación.")
        desc_actual = ""

    st.subheader("4) Cantidad, costo, precio y margen")

    with st.form(f"form_{code}"):
        descripcion = st.text_input("Descripción", value=desc_actual, placeholder="Ej: Coca Cola 1.5 L")

        modo_inventario = st.radio(
            "Inventario",
            ["Sumar compra al inventario", "Reemplazar inventario", "No modificar inventario"],
            index=0,
        )

        if modo_inventario == "Sumar compra al inventario":
            cantidad_comprada = st.number_input("Cantidad comprada", min_value=0.0, step=1.0, value=1.0)
            stock_final = stock_actual + cantidad_comprada if exists else cantidad_comprada
        elif modo_inventario == "Reemplazar inventario":
            stock_final = st.number_input("Nuevo stock final", min_value=0.0, step=1.0, value=stock_actual)
            cantidad_comprada = max(stock_final - stock_actual, 0)
        else:
            cantidad_comprada = 0.0
            stock_final = stock_actual

        c1, c2 = st.columns(2)
        with c1:
            costo_nuevo = st.number_input("Nuevo precio costo", min_value=0.0, step=10.0, value=float(costo_actual))
        with c2:
            precio_nuevo = st.number_input("Nuevo precio venta", min_value=0.0, step=10.0, value=float(venta_actual))

        margen_nuevo = margin_on_sale(costo_nuevo, precio_nuevo)
        markup_nuevo = markup_on_cost(costo_nuevo, precio_nuevo)
        utilidad = precio_nuevo - costo_nuevo

        m1, m2, m3 = st.columns(3)
        m1.metric("Nuevo margen", pct(margen_nuevo))
        m2.metric("Markup", pct(markup_nuevo))
        m3.metric("Utilidad/u", clp(utilidad))

        st.caption(f"Stock final que irá al Excel: {stock_final:g}")

        st.markdown("**Precio sugerido por margen objetivo**")
        margen_objetivo = st.slider("Margen objetivo sobre precio de venta", min_value=5, max_value=70, value=35, step=1)
        sugerido = price_from_margin(costo_nuevo, margen_objetivo)
        st.info(f"Con costo {clp(costo_nuevo)} y margen {margen_objetivo}%, el precio sugerido es {clp(sugerido)}.")

        mantener_margen = False
        if exists and margen_actual > 0:
            precio_mantener = price_from_margin(costo_nuevo, margen_actual)
            mantener_margen = st.checkbox(f"Mantener margen actual ({pct(margen_actual)}) → precio sugerido {clp(precio_mantener)}")
        else:
            precio_mantener = precio_nuevo

        mayoreo_nuevo = st.number_input("Precio mayoreo opcional", min_value=0.0, step=10.0, value=float(mayoreo_actual))
        departamento = st.text_input("Departamento", value=depto_actual)
        tipo_venta = st.text_input("Tipo de venta", value=tipo_actual or "Unidad")

        a1, a2, a3 = st.columns(3)
        with a1:
            actualizar_inventario = st.checkbox("Actualizar inventario", value=(modo_inventario != "No modificar inventario"))
        with a2:
            actualizar_costo = st.checkbox("Actualizar costo", value=True)
        with a3:
            actualizar_venta = st.checkbox("Actualizar venta", value=True)

        submitted = st.form_submit_button("💾 Guardar producto para Excel", type="primary")

    if submitted:
        if mantener_margen:
            precio_nuevo = round(precio_mantener)
            margen_nuevo = margin_on_sale(costo_nuevo, precio_nuevo)
            utilidad = precio_nuevo - costo_nuevo

        update = {
            "codigo": code,
            "producto_nuevo": not exists,
            "descripcion": descripcion,
            "cantidad_comprada": cantidad_comprada,
            "stock_actual": stock_actual,
            "stock_final": stock_final,
            "costo": costo_nuevo if actualizar_costo else costo_actual,
            "venta": precio_nuevo if actualizar_venta else venta_actual,
            "mayoreo": mayoreo_nuevo if mayoreo_nuevo > 0 else "",
            "departamento": departamento,
            "tipo_venta": tipo_venta,
            "margen": margen_nuevo,
            "utilidad": utilidad,
            "actualizar_inventario": actualizar_inventario,
            "actualizar_costo": actualizar_costo,
            "actualizar_venta": actualizar_venta,
            "actualizar_mayoreo": mayoreo_nuevo > 0,
            "actualizar_descripcion": True,
            "actualizar_departamento": bool(departamento),
            "actualizar_tipo_venta": bool(tipo_venta),
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

        existing_idx = next((i for i, u in enumerate(st.session_state.updates) if clean_code(u.get("codigo", "")) == code), None)
        if existing_idx is not None:
            old = st.session_state.updates[existing_idx]
            if modo_inventario == "Sumar compra al inventario":
                total_compra = to_number(old.get("cantidad_comprada", 0), 0) + to_number(cantidad_comprada, 0)
                update["cantidad_comprada"] = total_compra
                update["stock_final"] = stock_actual + total_compra if exists else total_compra
            st.session_state.updates[existing_idx] = update
        else:
            st.session_state.updates.append(update)

        st.success("Producto guardado. Puedes seguir escaneando.")
        st.session_state.current_code = ""
        st.rerun()

st.subheader("5) Productos guardados para importar")
if st.session_state.updates:
    view = pd.DataFrame(st.session_state.updates)
    show_cols = ["codigo", "descripcion", "producto_nuevo", "cantidad_comprada", "stock_actual", "stock_final", "costo", "venta", "margen", "utilidad"]
    show_cols = [c for c in show_cols if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, hide_index=True)

    total_costo = sum(to_number(u.get("cantidad_comprada", 0), 0) * to_number(u.get("costo", 0), 0) for u in st.session_state.updates)
    st.metric("Total estimado de compra registrada", clp(total_costo))

    c1, c2 = st.columns(2)
    with c1:
        if st.button("🧹 Limpiar lista"):
            st.session_state.updates = []
            st.rerun()
    with c2:
        only_updates = st.checkbox("Exportar solo productos escaneados", value=True)

    try:
        excel = build_export(df, st.session_state.updates, mapping, only_updates=only_updates)
        filename = f"eleventa_importacion_la_despensa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        st.download_button(
            "⬇️ Descargar Excel para importar en Eleventa",
            data=excel,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.caption("En Eleventa importa el archivo marcando actualizar productos existentes y actualizar inventario cuando corresponda.")
    except Exception as exc:
        st.error(f"No pude generar el Excel: {exc}")
else:
    st.info("Todavía no hay productos guardados.")

st.divider()
st.caption("La Despensa Minimarket · Prototipo celular para compras e importación a Eleventa")
