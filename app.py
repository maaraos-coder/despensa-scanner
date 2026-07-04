import base64
import io
import json
from datetime import datetime

import pandas as pd
import streamlit as st
from streamlit.components.v1 import html
from streamlit_js_eval import streamlit_js_eval

st.set_page_config(
    page_title="La Despensa Scanner",
    page_icon="🛒",
    layout="centered",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
    .main {max-width: 760px; margin: auto;}
    .big-card {
        padding: 16px;
        border-radius: 14px;
        border: 1px solid #ddd;
        background: #fafafa;
        margin-bottom: 12px;
    }
    .product-title {font-size: 1.25rem; font-weight: 800;}
    .small-muted {color: #666; font-size: 0.9rem;}
    button[kind="primary"] {width: 100%;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("🛒 La Despensa Scanner")
st.caption("Escanear productos → actualizar costo/venta/cantidad → generar Excel para Eleventa")

DEFAULT_ALIASES = {
    "codigo": ["codigo", "código", "codigo de barras", "código de barras", "codigo producto", "código producto", "clave", "barcode", "sku"],
    "descripcion": ["descripcion", "descripción", "producto", "nombre", "articulo", "artículo", "descripcion del producto", "descripción del producto"],
    "costo": ["costo", "precio costo", "precio de costo", "costo unitario", "precio_compra", "precio compra"],
    "venta": ["precio venta", "precio de venta", "venta", "precio", "precio publico", "precio público", "pventa"],
    "mayoreo": ["precio mayoreo", "precio de mayoreo", "mayoreo", "precio mayorista"],
    "inventario": ["inventario", "existencia", "existencia actual", "cantidad", "hay", "stock"],
    "departamento": ["departamento", "categoria", "categoría", "familia"],
    "tipo_venta": ["tipo de venta", "tipo venta", "se vende", "unidad", "tipo"],
}


def normalize_text(x: str) -> str:
    return str(x).strip().lower().replace("_", " ").replace("-", " ")


def guess_column(columns, key):
    normalized = {normalize_text(c): c for c in columns}
    for alias in DEFAULT_ALIASES[key]:
        if alias in normalized:
            return normalized[alias]
    # fuzzy contains
    for c in columns:
        nc = normalize_text(c)
        for alias in DEFAULT_ALIASES[key]:
            if alias in nc or nc in alias:
                return c
    return None


def read_excel(uploaded):
    return pd.read_excel(uploaded, dtype=str).fillna("")


def to_number(value, default=0.0):
    try:
        s = str(value).replace("$", "").replace(".", "").replace(",", ".").strip()
        if s == "":
            return default
        return float(s)
    except Exception:
        return default


def format_clp(value):
    try:
        return "$" + f"{int(round(float(value))):,}".replace(",", ".")
    except Exception:
        return "$0"


def find_product(df, col_code, code):
    if not col_code or df.empty:
        return None
    code = str(code).strip()
    matches = df[df[col_code].astype(str).str.strip() == code]
    if matches.empty:
        return None
    return matches.iloc[0].to_dict()


def make_excel(df_original, updates, mapping, only_updates=True):
    df = df_original.copy()
    col_code = mapping.get("codigo")
    if not col_code:
        raise ValueError("No se identificó la columna de código.")

    for upd in updates:
        code = str(upd["codigo"]).strip()
        idx = df[df[col_code].astype(str).str.strip() == code].index
        if len(idx) == 0:
            # producto nuevo: crear fila mínima usando columnas existentes
            new_row = {c: "" for c in df.columns}
            new_row[col_code] = code
            for field in ["descripcion", "costo", "venta", "mayoreo", "inventario", "departamento", "tipo_venta"]:
                col = mapping.get(field)
                if col and field in upd:
                    new_row[col] = upd[field]
            df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
            continue
        i = idx[0]
        for field in ["descripcion", "costo", "venta", "mayoreo", "inventario", "departamento", "tipo_venta"]:
            col = mapping.get(field)
            if col and field in upd and upd[field] not in [None, ""]:
                if field == "inventario":
                    actual = to_number(df.at[i, col], 0)
                    agregar = to_number(upd[field], 0)
                    df.at[i, col] = actual + agregar
                else:
                    df.at[i, col] = upd[field]

    if only_updates:
        codes = {str(u["codigo"]).strip() for u in updates}
        df = df[df[col_code].astype(str).str.strip().isin(codes)]

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Productos")
    output.seek(0)
    return output


def html5_scanner_component(component_key="scanner"):
    # Uses Html5Qrcode from CDN. Works in HTTPS; Streamlit Cloud is HTTPS.
    component_id = f"scanner_{component_key}"
    html_code = f"""
    <div style="font-family: sans-serif;">
      <div id="{component_id}" style="width:100%; max-width:520px; margin:auto;"></div>
      <div style="display:flex; gap:8px; margin-top:10px;">
        <button id="startBtn" style="flex:1; padding:12px; border-radius:10px; border:0; background:#111; color:white; font-weight:700;">📷 Iniciar cámara</button>
        <button id="stopBtn" style="flex:1; padding:12px; border-radius:10px; border:1px solid #999; background:white; color:#111; font-weight:700;">Detener</button>
      </div>
      <p id="status" style="color:#555; font-size:14px;">Presiona iniciar cámara y apunta al código de barras.</p>
    </div>
    <script src="https://unpkg.com/html5-qrcode" type="text/javascript"></script>
    <script>
      let qrScanner = null;
      let running = false;
      const statusEl = document.getElementById('status');
      const startBtn = document.getElementById('startBtn');
      const stopBtn = document.getElementById('stopBtn');

      function sendCode(code) {{
        const msg = {{isStreamlitMessage: true, type: 'streamlit:setComponentValue', value: code}};
        window.parent.postMessage(msg, '*');
      }}

      async function startScanner() {{
        try {{
          if (running) return;
          qrScanner = new Html5Qrcode("{component_id}");
          const config = {{ fps: 10, qrbox: {{ width: 280, height: 160 }}, aspectRatio: 1.333 }};
          await qrScanner.start(
            {{ facingMode: "environment" }},
            config,
            (decodedText, decodedResult) => {{
              statusEl.innerText = "Código leído: " + decodedText;
              sendCode(decodedText);
            }},
            (errorMessage) => {{}}
          );
          running = true;
          statusEl.innerText = "Cámara activa. Apunta al código.";
        }} catch (err) {{
          statusEl.innerText = "No se pudo iniciar la cámara. Usa HTTPS y da permiso de cámara.";
          console.error(err);
        }}
      }}

      async function stopScanner() {{
        try {{
          if (qrScanner && running) {{
            await qrScanner.stop();
            await qrScanner.clear();
            running = false;
            statusEl.innerText = "Cámara detenida.";
          }}
        }} catch (err) {{ console.error(err); }}
      }}

      startBtn.addEventListener('click', startScanner);
      stopBtn.addEventListener('click', stopScanner);
    </script>
    """
    return html(html_code, height=520)


if "updates" not in st.session_state:
    st.session_state.updates = []
if "last_code" not in st.session_state:
    st.session_state.last_code = ""

with st.expander("1) Subir catálogo/exportación de Eleventa", expanded=True):
    uploaded = st.file_uploader("Sube el Excel exportado desde Eleventa", type=["xlsx", "xls"])

if not uploaded:
    st.info("Primero sube el Excel de productos de Eleventa. Después podrás escanear desde el celular.")
    st.stop()

try:
    df = read_excel(uploaded)
except Exception as e:
    st.error(f"No pude leer el Excel: {e}")
    st.stop()

st.success(f"Catálogo cargado: {len(df)} filas")

st.subheader("2) Columnas detectadas")
cols = list(df.columns)

mapping = {}
for key, label in [
    ("codigo", "Código de barras"),
    ("descripcion", "Descripción"),
    ("costo", "Precio costo"),
    ("venta", "Precio venta"),
    ("mayoreo", "Precio mayoreo"),
    ("inventario", "Inventario / existencia"),
    ("departamento", "Departamento"),
    ("tipo_venta", "Tipo de venta"),
]:
    guessed = guess_column(cols, key)
    idx = cols.index(guessed) if guessed in cols else 0
    mapping[key] = st.selectbox(label, options=[""] + cols, index=(idx + 1 if guessed else 0), key=f"map_{key}")

if not mapping.get("codigo"):
    st.error("Debes seleccionar la columna del código de barras.")
    st.stop()

st.subheader("3) Escanear con cámara")
st.caption("En iPhone/Android debe estar publicado en HTTPS, por ejemplo Streamlit Cloud.")
html5_scanner_component("main")

manual_code = st.text_input("Código leído o ingreso manual", value=st.session_state.last_code, placeholder="Escanea o escribe el código")
if manual_code:
    st.session_state.last_code = manual_code.strip()

code = st.session_state.last_code.strip()
product = find_product(df, mapping["codigo"], code) if code else None

if code:
    if product:
        desc = product.get(mapping.get("descripcion", ""), "") if mapping.get("descripcion") else ""
        costo_actual = product.get(mapping.get("costo", ""), "") if mapping.get("costo") else ""
        venta_actual = product.get(mapping.get("venta", ""), "") if mapping.get("venta") else ""
        inv_actual = product.get(mapping.get("inventario", ""), "") if mapping.get("inventario") else ""
        st.markdown(
            f"""
            <div class="big-card">
              <div class="product-title">{desc if desc else 'Producto encontrado'}</div>
              <div class="small-muted">Código: {code}</div>
              <div>Stock actual: <b>{inv_actual}</b></div>
              <div>Costo actual: <b>{costo_actual}</b></div>
              <div>Venta actual: <b>{venta_actual}</b></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    else:
        st.warning("Producto no encontrado. Puedes agregarlo como producto nuevo para el Excel.")
        desc = ""
        costo_actual = ""
        venta_actual = ""
        inv_actual = ""

    st.subheader("4) Actualizar producto")
    with st.form("update_form", clear_on_submit=False):
        descripcion = st.text_input("Descripción", value=str(desc))
        cantidad = st.number_input("Cantidad comprada / agregar a inventario", min_value=0.0, step=1.0, value=1.0)
        costo = st.number_input("Nuevo precio costo", min_value=0.0, step=10.0, value=to_number(costo_actual, 0))
        venta = st.number_input("Nuevo precio venta", min_value=0.0, step=10.0, value=to_number(venta_actual, 0))
        mayoreo = st.number_input("Precio mayoreo opcional", min_value=0.0, step=10.0, value=0.0)
        departamento = st.text_input("Departamento opcional", value=str(product.get(mapping.get("departamento", ""), "") if product and mapping.get("departamento") else ""))
        tipo_venta = st.text_input("Tipo de venta opcional", value=str(product.get(mapping.get("tipo_venta", ""), "") if product and mapping.get("tipo_venta") else ""))
        submitted = st.form_submit_button("💾 Guardar producto", type="primary")

    if submitted:
        upd = {
            "codigo": code,
            "descripcion": descripcion,
            "inventario": cantidad,
            "costo": costo,
            "venta": venta,
            "mayoreo": mayoreo if mayoreo > 0 else "",
            "departamento": departamento,
            "tipo_venta": tipo_venta,
            "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        # replace previous update for same code or append accumulating quantity
        existing = next((i for i, u in enumerate(st.session_state.updates) if str(u["codigo"]) == str(code)), None)
        if existing is not None:
            old = st.session_state.updates[existing]
            upd["inventario"] = to_number(old.get("inventario", 0), 0) + to_number(cantidad, 0)
            st.session_state.updates[existing] = upd
        else:
            st.session_state.updates.append(upd)
        st.success("Producto guardado para exportar.")
        st.session_state.last_code = ""
        st.rerun()

st.subheader("5) Productos guardados")
if st.session_state.updates:
    updates_df = pd.DataFrame(st.session_state.updates)
    st.dataframe(updates_df, use_container_width=True)

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("🧹 Limpiar lista"):
            st.session_state.updates = []
            st.rerun()

    with col_b:
        only_updates = st.checkbox("Exportar solo productos modificados", value=True)

    try:
        excel_file = make_excel(df, st.session_state.updates, mapping, only_updates=only_updates)
        filename = f"eleventa_importacion_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
        st.download_button(
            "⬇️ Descargar Excel para Eleventa",
            data=excel_file,
            file_name=filename,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
    except Exception as e:
        st.error(f"No pude generar el Excel: {e}")
else:
    st.info("Todavía no hay productos guardados.")

st.divider()
st.caption("La Despensa Minimarket · Prototipo inicial · Genera Excel compatible mediante columnas de Eleventa")
