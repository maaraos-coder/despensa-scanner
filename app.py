import io
import re
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="La Despensa Compras", page_icon="🛒", layout="centered")

# ------------------------- Helpers -------------------------
def norm_col(c: str) -> str:
    c = str(c).strip().lower()
    c = c.replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u")
    c = re.sub(r"[^a-z0-9]+", "_", c).strip("_")
    return c


def money(x):
    try:
        return f"${float(x):,.0f}".replace(",", ".")
    except Exception:
        return "$0"


def safe_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        if isinstance(x, str):
            x = x.replace("$", "").replace(".", "").replace(",", ".").strip()
        return float(x)
    except Exception:
        return default


def margin(costo, venta):
    costo = safe_float(costo)
    venta = safe_float(venta)
    if venta <= 0:
        return 0.0
    return (venta - costo) / venta * 100


def markup(costo, venta):
    costo = safe_float(costo)
    venta = safe_float(venta)
    if costo <= 0:
        return 0.0
    return (venta - costo) / costo * 100


def suggested_price_from_margin(costo, target_margin):
    costo = safe_float(costo)
    target_margin = safe_float(target_margin)
    if target_margin >= 100:
        return costo
    return costo / (1 - target_margin / 100)


def detect_columns(df: pd.DataFrame) -> dict:
    ncols = {norm_col(c): c for c in df.columns}

    def pick(options):
        for opt in options:
            if opt in ncols:
                return ncols[opt]
        for key, original in ncols.items():
            for opt in options:
                if opt in key:
                    return original
        return None

    return {
        "codigo": pick(["codigo", "codigo_de_barras", "codigo_producto", "cod_barras", "barcode"]),
        "descripcion": pick(["descripcion", "descripcion_del_producto", "producto", "nombre"]),
        "costo": pick(["precio_de_costo", "precio_costo", "costo", "precio_compra"]),
        "venta": pick(["precio_de_venta", "precio_venta", "venta", "precio"]),
        "mayoreo": pick(["precio_mayoreo", "precio_de_mayoreo", "mayoreo"]),
        "inventario": pick(["inventario", "cantidad", "existencia", "existencia_actual", "hay", "stock"]),
        "departamento": pick(["departamento", "categoria", "familia"]),
        "tipo_venta": pick(["tipo_de_venta", "tipo_venta", "se_vende", "unidad"]),
        "minimo": pick(["minimo", "inventario_minimo", "minimo_de_inventario"]),
        "maximo": pick(["maximo", "inventario_maximo", "maximo_de_inventario"]),
    }


def find_product(df, cols, code: str) -> Optional[pd.Series]:
    if df is None or not cols.get("codigo"):
        return None
    code = str(code).strip()
    s = df[cols["codigo"]].astype(str).str.strip()
    matches = df[s == code]
    if len(matches) == 0:
        return None
    return matches.iloc[0]


def scanner_html(height=620):
    # ZXing scanner with focus/zoom/torch controls. Redirects code into query param.
    return f"""
<div id="scanner-wrap" style="font-family: system-ui, -apple-system, Segoe UI, sans-serif;">
  <div style="font-weight:700;margin-bottom:8px;">📷 Escáner de código de barras</div>
  <div id="status" style="padding:8px;border-radius:8px;background:#fff3cd;color:#5c4500;margin-bottom:8px;">
    Iniciando cámara trasera...
  </div>
  <video id="video" playsinline muted autoplay style="width:100%;max-height:430px;border-radius:14px;background:#000;object-fit:cover;"></video>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:10px;">
    <button id="startBtn" style="padding:10px;border-radius:10px;border:0;background:#111;color:white;">Reiniciar cámara</button>
    <button id="torchBtn" style="padding:10px;border-radius:10px;border:0;background:#f5c542;color:#111;display:none;">Linterna</button>
  </div>
  <div id="zoomBox" style="display:none;margin-top:10px;">
    <label>Zoom / enfoque: <span id="zoomVal"></span></label>
    <input id="zoom" type="range" style="width:100%;" />
  </div>
  <div style="font-size:13px;color:#555;margin-top:10px;line-height:1.35;">
    Consejo iPhone: usa buena luz, pon el código completo dentro de la pantalla y espera 1–2 segundos. Si no enfoca, aleja un poco y vuelve a acercar.
  </div>
</div>
<script src="https://unpkg.com/@zxing/library@0.21.3/umd/index.min.js"></script>
<script>
let codeReader = null;
let stream = null;
let track = null;
let scanning = false;
let torchOn = false;

const video = document.getElementById('video');
const statusBox = document.getElementById('status');
const startBtn = document.getElementById('startBtn');
const torchBtn = document.getElementById('torchBtn');
const zoomBox = document.getElementById('zoomBox');
const zoomInput = document.getElementById('zoom');
const zoomVal = document.getElementById('zoomVal');

function setStatus(msg, type='warn') {{
  let bg = '#fff3cd', color = '#5c4500';
  if (type === 'ok') {{ bg = '#d1e7dd'; color = '#0f5132'; }}
  if (type === 'err') {{ bg = '#f8d7da'; color = '#842029'; }}
  if (type === 'info') {{ bg = '#cff4fc'; color = '#055160'; }}
  statusBox.style.background = bg;
  statusBox.style.color = color;
  statusBox.innerText = msg;
}}

function beep() {{
  try {{
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.connect(gain); gain.connect(ctx.destination);
    osc.frequency.value = 880; gain.gain.value = 0.12;
    osc.start(); setTimeout(() => {{ osc.stop(); ctx.close(); }}, 120);
  }} catch(e) {{}}
}}

async function stopAll() {{
  scanning = false;
  try {{ if (codeReader) codeReader.reset(); }} catch(e) {{}}
  try {{ if (stream) stream.getTracks().forEach(t => t.stop()); }} catch(e) {{}}
}}

async function setupCamera() {{
  await stopAll();
  setStatus('Solicitando cámara trasera...', 'info');

  const constraints = {{
    audio: false,
    video: {{
      facingMode: {{ ideal: 'environment' }},
      width: {{ ideal: 1920 }},
      height: {{ ideal: 1080 }},
      focusMode: {{ ideal: 'continuous' }}
    }}
  }};

  try {{
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    video.srcObject = stream;
    await video.play();
    track = stream.getVideoTracks()[0];

    // Try continuous autofocus and exposure when supported
    try {{
      const caps = track.getCapabilities ? track.getCapabilities() : {{}};
      const apply = {{ advanced: [] }};
      if (caps.focusMode && caps.focusMode.includes('continuous')) apply.advanced.push({{ focusMode: 'continuous' }});
      if (caps.exposureMode && caps.exposureMode.includes('continuous')) apply.advanced.push({{ exposureMode: 'continuous' }});
      if (caps.whiteBalanceMode && caps.whiteBalanceMode.includes('continuous')) apply.advanced.push({{ whiteBalanceMode: 'continuous' }});
      if (apply.advanced.length) await track.applyConstraints(apply);

      if (caps.torch) {{ torchBtn.style.display = 'inline-block'; }}
      else {{ torchBtn.style.display = 'none'; }}

      if (caps.zoom) {{
        zoomBox.style.display = 'block';
        zoomInput.min = caps.zoom.min;
        zoomInput.max = caps.zoom.max;
        zoomInput.step = caps.zoom.step || 0.1;
        zoomInput.value = Math.min(caps.zoom.max, Math.max(caps.zoom.min, 1.5));
        zoomVal.innerText = zoomInput.value;
        await track.applyConstraints({{ advanced: [{{ zoom: Number(zoomInput.value) }}] }});
      }} else {{
        zoomBox.style.display = 'none';
      }}
    }} catch(e) {{ console.log('Capabilities not fully supported', e); }}

    setStatus('Cámara lista. Apunta al código de barras.', 'ok');
    startScan();
  }} catch (err) {{
    setStatus('No se pudo abrir la cámara. Revisa permisos de Safari/Chrome y que estés en HTTPS. Error: ' + err.message, 'err');
  }}
}}

function startScan() {{
  scanning = true;
  codeReader = new ZXing.BrowserMultiFormatReader(undefined, 250);
  const hints = new Map();
  hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [
    ZXing.BarcodeFormat.EAN_13,
    ZXing.BarcodeFormat.EAN_8,
    ZXing.BarcodeFormat.UPC_A,
    ZXing.BarcodeFormat.UPC_E,
    ZXing.BarcodeFormat.CODE_128,
    ZXing.BarcodeFormat.CODE_39,
    ZXing.BarcodeFormat.ITF,
    ZXing.BarcodeFormat.QR_CODE
  ]);
  codeReader.hints = hints;

  codeReader.decodeFromVideoElementContinuously(video, async (result, err) => {{
    if (result && scanning) {{
      const code = result.getText();
      scanning = false;
      beep();
      setStatus('✅ Código leído: ' + code, 'ok');
      await stopAll();
      const url = new URL(window.parent.location.href);
      url.searchParams.set('scanned_code', code);
      url.searchParams.set('t', Date.now().toString());
      window.parent.location.href = url.toString();
    }}
  }});
}}

startBtn.onclick = setupCamera;
torchBtn.onclick = async () => {{
  if (!track) return;
  torchOn = !torchOn;
  try {{
    await track.applyConstraints({{ advanced: [{{ torch: torchOn }}] }});
    torchBtn.innerText = torchOn ? 'Apagar linterna' : 'Linterna';
  }} catch(e) {{ setStatus('Tu navegador no permitió activar la linterna.', 'err'); }}
}};
zoomInput.oninput = async () => {{
  zoomVal.innerText = zoomInput.value;
  try {{ if (track) await track.applyConstraints({{ advanced: [{{ zoom: Number(zoomInput.value) }}] }}); }} catch(e) {{}}
}};

if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {{
  setStatus('Este navegador no permite usar cámara desde la web.', 'err');
}} else {{
  setupCamera();
}}
</script>
"""

# ------------------------- Session -------------------------
if "items" not in st.session_state:
    st.session_state.items = []
if "last_code" not in st.session_state:
    st.session_state.last_code = ""

# Read scanned code from query param
try:
    qp_code = st.query_params.get("scanned_code", "")
except Exception:
    qp_code = ""
if qp_code:
    st.session_state.last_code = str(qp_code).strip()

st.title("🛒 La Despensa Compras")
st.caption("Escanea productos, suma inventario y genera Excel para importar en Eleventa.")

with st.expander("1) Cargar catálogo exportado de Eleventa", expanded=True):
    file = st.file_uploader("Sube el Excel exportado desde Eleventa", type=["xlsx", "xls"])
    if file:
        df = pd.read_excel(file)
        cols = detect_columns(df)
        st.session_state.catalog = df
        st.session_state.cols = cols
        st.success(f"Catálogo cargado: {len(df)} productos")
        st.write("Columnas detectadas:")
        st.json(cols)
    else:
        st.info("Puedes probar igual, pero para sumar inventario actual debes cargar primero el catálogo de Eleventa.")
        st.session_state.catalog = None
        st.session_state.cols = {}

st.divider()
st.subheader("2) Escanear con cámara")

components.html(scanner_html(), height=620, scrolling=False)

manual_code = st.text_input("Código leído / manual", value=st.session_state.last_code, placeholder="Escanea o escribe el código")
if manual_code:
    st.session_state.last_code = manual_code.strip()

code = st.session_state.last_code.strip()
if code:
    df = st.session_state.get("catalog")
    cols = st.session_state.get("cols", {})
    product = find_product(df, cols, code)

    st.divider()
    if product is not None:
        desc = str(product.get(cols.get("descripcion"), "Producto sin descripción"))
        stock_actual = safe_float(product.get(cols.get("inventario"), 0)) if cols.get("inventario") else 0
        costo_actual = safe_float(product.get(cols.get("costo"), 0)) if cols.get("costo") else 0
        venta_actual = safe_float(product.get(cols.get("venta"), 0)) if cols.get("venta") else 0
        mayoreo_actual = safe_float(product.get(cols.get("mayoreo"), 0)) if cols.get("mayoreo") else 0
        depto = str(product.get(cols.get("departamento"), "")) if cols.get("departamento") else ""

        st.success(f"Producto encontrado: {desc}")
        c1, c2, c3 = st.columns(3)
        c1.metric("Stock actual", f"{stock_actual:g}")
        c2.metric("Costo actual", money(costo_actual))
        c3.metric("Venta actual", money(venta_actual))
        st.metric("Margen actual", f"{margin(costo_actual, venta_actual):.1f}%")

        with st.form("form_existente", clear_on_submit=False):
            cantidad_compra = st.number_input("Cantidad comprada", min_value=0.0, value=1.0, step=1.0)
            modo = st.radio("Inventario", ["Sumar al inventario", "Reemplazar inventario", "No modificar inventario"], horizontal=False)
            nuevo_costo = st.number_input("Nuevo costo", min_value=0.0, value=float(costo_actual), step=10.0)
            mantener = st.checkbox("Mantener margen actual", value=False)
            margen_obj = st.number_input("Margen objetivo (%)", min_value=0.0, max_value=95.0, value=float(round(margin(costo_actual, venta_actual) or 30, 1)), step=1.0)
            if mantener:
                nuevo_precio_default = suggested_price_from_margin(nuevo_costo, margen_obj)
            else:
                nuevo_precio_default = venta_actual
            nuevo_precio = st.number_input("Nuevo precio venta", min_value=0.0, value=float(round(nuevo_precio_default)), step=10.0)
            nuevo_mayoreo = st.number_input("Nuevo precio mayoreo", min_value=0.0, value=float(mayoreo_actual), step=10.0)

            if modo == "Sumar al inventario":
                stock_final = stock_actual + cantidad_compra
            elif modo == "Reemplazar inventario":
                stock_final = cantidad_compra
            else:
                stock_final = stock_actual

            st.info(f"Stock final: {stock_final:g} | Margen nuevo: {margin(nuevo_costo, nuevo_precio):.1f}% | Utilidad: {money(nuevo_precio - nuevo_costo)}")
            guardar = st.form_submit_button("Guardar producto actualizado")

        if guardar:
            st.session_state.items.append({
                "Código": code,
                "Descripción": desc,
                "Precio de costo": nuevo_costo,
                "Precio de venta": nuevo_precio,
                "Precio mayoreo": nuevo_mayoreo,
                "Departamento": depto,
                "Cantidad en inventario": stock_final,
                "Cantidad comprada": cantidad_compra,
                "Tipo de venta": str(product.get(cols.get("tipo_venta"), "Unidad")) if cols.get("tipo_venta") else "Unidad",
                "Nuevo producto": "No",
            })
            st.success("Producto guardado en la compra.")
            st.session_state.last_code = ""
            try:
                st.query_params.clear()
            except Exception:
                pass

    else:
        st.warning("Código no encontrado en el catálogo. Puedes crear un producto nuevo.")
        with st.form("form_nuevo", clear_on_submit=False):
            desc = st.text_input("Descripción del producto", value="")
            cantidad = st.number_input("Cantidad inicial / comprada", min_value=0.0, value=1.0, step=1.0)
            costo = st.number_input("Precio de costo", min_value=0.0, value=0.0, step=10.0)
            margen_obj = st.number_input("Margen objetivo (%)", min_value=0.0, max_value=95.0, value=30.0, step=1.0)
            precio_sugerido = suggested_price_from_margin(costo, margen_obj)
            venta = st.number_input("Precio de venta", min_value=0.0, value=float(round(precio_sugerido)), step=10.0)
            mayoreo = st.number_input("Precio mayoreo", min_value=0.0, value=0.0, step=10.0)
            departamento = st.text_input("Departamento", value="")
            tipo_venta = st.selectbox("Tipo de venta", ["Unidad", "Granel"])
            usa_inv = st.checkbox("Usa inventario", value=True)
            st.info(f"Margen: {margin(costo, venta):.1f}% | Markup: {markup(costo, venta):.1f}% | Utilidad: {money(venta-costo)}")
            crear = st.form_submit_button("Crear producto nuevo")
        if crear and desc.strip():
            st.session_state.items.append({
                "Código": code,
                "Descripción": desc.strip(),
                "Precio de costo": costo,
                "Precio de venta": venta,
                "Precio mayoreo": mayoreo,
                "Departamento": departamento,
                "Cantidad en inventario": cantidad if usa_inv else 0,
                "Cantidad comprada": cantidad,
                "Tipo de venta": tipo_venta,
                "Nuevo producto": "Sí",
            })
            st.success("Producto nuevo guardado en la compra.")
            st.session_state.last_code = ""
            try:
                st.query_params.clear()
            except Exception:
                pass

st.divider()
st.subheader("3) Productos de esta compra")
if st.session_state.items:
    out_df = pd.DataFrame(st.session_state.items)
    st.dataframe(out_df, use_container_width=True)

    # Export only scanned/created products
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
        out_df.to_excel(writer, index=False, sheet_name="Importar_Eleventa")
    buf.seek(0)
    st.download_button(
        "⬇️ Descargar Excel para importar en Eleventa",
        data=buf,
        file_name=f"importacion_eleventa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    if st.button("Vaciar compra"):
        st.session_state.items = []
        st.rerun()
else:
    st.info("Aún no hay productos guardados.")

