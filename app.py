import streamlit as st
import pandas as pd
from io import BytesIO
from datetime import datetime
import streamlit.components.v1 as components

st.set_page_config(page_title="La Despensa Compras", page_icon="📦", layout="centered")
st.title("📦 La Despensa Compras")
st.caption("Escanea productos, suma inventario, ajusta costos/precios y exporta Excel para Eleventa.")

# ---------- Helpers ----------
def norm(s):
    return str(s).strip().lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")

def find_col(df, options):
    cols = list(df.columns)
    nmap = {norm(c): c for c in cols}
    for opt in options:
        for k, c in nmap.items():
            if opt in k:
                return c
    return None

def money(x):
    try:
        return int(round(float(x)))
    except Exception:
        return 0

def margen(costo, venta):
    try:
        costo = float(costo); venta = float(venta)
        if venta <= 0: return 0.0
        return (venta - costo) / venta * 100
    except Exception:
        return 0.0

def precio_por_margen(costo, margen_obj):
    try:
        costo = float(costo); margen_obj = float(margen_obj)
        if margen_obj >= 100: return costo
        return round(costo / (1 - margen_obj/100))
    except Exception:
        return 0

# ---------- Session ----------
if "catalogo" not in st.session_state: st.session_state.catalogo = None
if "cols" not in st.session_state: st.session_state.cols = {}
if "items" not in st.session_state: st.session_state.items = []

# Query param from scanner
try:
    qp = st.query_params
    scanned_qp = qp.get("barcode", "")
except Exception:
    scanned_qp = ""
if isinstance(scanned_qp, list):
    scanned_qp = scanned_qp[0] if scanned_qp else ""

with st.expander("1) Cargar catálogo/exportación Eleventa", expanded=st.session_state.catalogo is None):
    uploaded = st.file_uploader("Sube el Excel exportado desde Eleventa", type=["xlsx", "xls", "csv"])
    if uploaded:
        if uploaded.name.lower().endswith("csv"):
            df = pd.read_csv(uploaded)
        else:
            df = pd.read_excel(uploaded)
        df.columns = [str(c).strip() for c in df.columns]
        st.session_state.catalogo = df
        st.session_state.cols = {
            "codigo": find_col(df, ["codigo", "barra", "barcode", "cod"]),
            "descripcion": find_col(df, ["descripcion", "producto", "nombre", "articulo"]),
            "costo": find_col(df, ["costo"]),
            "venta": find_col(df, ["venta", "precio"]),
            "mayoreo": find_col(df, ["mayoreo", "mayorista"]),
            "inventario": find_col(df, ["inventario", "existencia", "hay", "cantidad"]),
            "departamento": find_col(df, ["departamento", "categoria"]),
            "tipo": find_col(df, ["tipo"]),
        }
        st.success(f"Catálogo cargado: {len(df)} productos")
        st.write("Columnas detectadas:", st.session_state.cols)

# ---------- Scanner HTML ----------
st.subheader("2) Escanear código")
st.info("En iPhone: acerca el código hasta llenar el recuadro, evita brillo/plástico arrugado y mantén el celular quieto 1–2 segundos.")

scanner_html = r'''
<div style="font-family: system-ui; width:100%; max-width:680px; margin:auto;">
  <div style="position:relative; background:#000; border-radius:20px; overflow:hidden; border:2px solid #111; min-height:360px;">
    <video id="video" playsinline muted autoplay style="width:100%; height:420px; object-fit:cover; background:#000;"></video>
    <div style="position:absolute; left:8%; right:8%; top:32%; height:34%; border:5px solid rgba(255,255,255,.95); border-radius:12px; box-shadow: 0 0 0 9999px rgba(0,0,0,.18);"></div>
    <div style="position:absolute; left:12%; right:12%; top:49%; border-top:3px solid rgba(255,60,60,.95);"></div>
  </div>
  <div style="display:flex; gap:10px; margin-top:12px;">
    <button id="startBtn" style="flex:1; font-size:20px; padding:16px; border-radius:14px; border:0; background:#111; color:white; font-weight:800;">📷 Iniciar cámara</button>
    <button id="stopBtn" style="flex:1; font-size:20px; padding:16px; border-radius:14px; border:1px solid #999; background:white; color:#111; font-weight:800;">Detener</button>
  </div>
  <div style="display:flex; gap:10px; margin-top:10px;">
    <button id="zoomIn" style="flex:1; font-size:18px; padding:12px; border-radius:12px; border:1px solid #bbb; background:#f2f2f2;">🔍 Zoom +</button>
    <button id="torchBtn" style="flex:1; font-size:18px; padding:12px; border-radius:12px; border:1px solid #bbb; background:#f2f2f2;">🔦 Linterna</button>
  </div>
  <div id="status" style="margin-top:12px; padding:14px; border-radius:14px; background:#f1f1f1; font-size:18px;">Estado: esperando inicio.</div>
</div>
<script src="https://unpkg.com/@zxing/browser@latest/umd/index.min.js"></script>
<script>
let stream = null;
let detector = null;
let interval = null;
let track = null;
let zoomLevel = 1;
let torchOn = false;
let zxingReader = null;
let zxingControls = null;
const video = document.getElementById('video');
const statusBox = document.getElementById('status');
function setStatus(t){ statusBox.innerText = t; }
function sendCode(code){
  code = String(code || '').trim();
  if(!code) return;
  setStatus('✅ Código leído: ' + code + ' — cargando producto...');
  try { if(navigator.vibrate) navigator.vibrate(120); } catch(e) {}
  stopCamera();
  const url = new URL(window.parent.location.href);
  url.searchParams.set('barcode', code);
  url.searchParams.set('t', Date.now().toString());
  window.parent.location.href = url.toString();
}
async function startCamera(){
  try{
    setStatus('Solicitando cámara trasera...');
    stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        focusMode: { ideal: 'continuous' },
        advanced: [{focusMode: 'continuous'}]
      }, audio:false
    });
    video.srcObject = stream;
    await video.play();
    track = stream.getVideoTracks()[0];
    setStatus('Cámara activa. Acerca el código y evita reflejos. Probando lector nativo...');
    if('BarcodeDetector' in window){
      detector = new BarcodeDetector({formats:['ean_13','ean_8','upc_a','upc_e','code_128','code_39','itf']});
      interval = setInterval(async()=>{
        try{
          const codes = await detector.detect(video);
          if(codes && codes.length){ sendCode(codes[0].rawValue); }
        }catch(e){ setStatus('Escaneando... mantén quieto el código.'); }
      }, 300);
    } else {
      setStatus('Lector nativo no disponible. Usando ZXing...');
      if(window.ZXingBrowser){
        zxingReader = new ZXingBrowser.BrowserMultiFormatReader();
        zxingControls = await zxingReader.decodeFromVideoDevice(undefined, video, (result, err, controls) => {
          if(result){ sendCode(result.getText()); }
        });
      } else {
        setStatus('No se pudo cargar el lector. Usa ingreso manual abajo.');
      }
    }
  }catch(err){
    setStatus('❌ No se pudo abrir cámara: ' + err.message + '. Revisa permisos en Safari/Chrome.');
  }
}
function stopCamera(){
  if(interval){ clearInterval(interval); interval=null; }
  try{ if(zxingControls) zxingControls.stop(); }catch(e){}
  if(stream){ stream.getTracks().forEach(t=>t.stop()); stream=null; }
  video.srcObject = null;
}
async function applyZoom(){
  try{
    if(!track) return;
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    if(caps.zoom){
      zoomLevel += 0.5;
      if(zoomLevel > caps.zoom.max) zoomLevel = caps.zoom.min || 1;
      await track.applyConstraints({advanced:[{zoom: zoomLevel}]});
      setStatus('Zoom aplicado: ' + zoomLevel.toFixed(1) + 'x');
    } else setStatus('Este navegador no permite zoom manual. Acerca físicamente el celular.');
  }catch(e){ setStatus('No se pudo aplicar zoom.'); }
}
async function toggleTorch(){
  try{
    if(!track) return;
    const caps = track.getCapabilities ? track.getCapabilities() : {};
    if(caps.torch){
      torchOn = !torchOn;
      await track.applyConstraints({advanced:[{torch: torchOn}]});
      setStatus(torchOn ? 'Linterna encendida.' : 'Linterna apagada.');
    } else setStatus('Linterna no disponible desde este navegador.');
  }catch(e){ setStatus('No se pudo usar linterna.'); }
}
document.getElementById('startBtn').onclick = startCamera;
document.getElementById('stopBtn').onclick = ()=>{ stopCamera(); setStatus('Cámara detenida.'); };
document.getElementById('zoomIn').onclick = applyZoom;
document.getElementById('torchBtn').onclick = toggleTorch;
</script>
'''
components.html(scanner_html, height=620)

manual_code = st.text_input("O ingresa el código manualmente", value=scanned_qp or "")
if st.button("Usar código", type="primary"):
    if manual_code.strip():
        st.query_params["barcode"] = manual_code.strip()
        st.rerun()

codigo = (scanned_qp or manual_code or "").strip()

# ---------- Product form ----------
if codigo:
    st.subheader(f"3) Producto: {codigo}")
    df = st.session_state.catalogo
    cols = st.session_state.cols
    existing = None
    if df is not None and cols.get("codigo"):
        mask = df[cols["codigo"]].astype(str).str.strip() == codigo
        if mask.any():
            existing = df[mask].iloc[0].to_dict()

    if existing:
        st.success("Producto encontrado en catálogo.")
        desc0 = str(existing.get(cols.get("descripcion"), ""))
        stock0 = float(existing.get(cols.get("inventario"), 0) or 0) if cols.get("inventario") else 0
        costo0 = money(existing.get(cols.get("costo"), 0)) if cols.get("costo") else 0
        venta0 = money(existing.get(cols.get("venta"), 0)) if cols.get("venta") else 0
        depto0 = str(existing.get(cols.get("departamento"), "")) if cols.get("departamento") else ""
    else:
        st.warning("Código no encontrado. Puedes crear producto nuevo.")
        desc0, stock0, costo0, venta0, depto0 = "", 0, 0, 0, ""

    with st.form("form_producto"):
        descripcion = st.text_input("Descripción", value=desc0)
        c1, c2 = st.columns(2)
        with c1:
            stock_actual = st.number_input("Stock actual", value=float(stock0), step=1.0)
            cantidad_compra = st.number_input("Cantidad comprada", value=1.0, step=1.0, min_value=0.0)
            modo = st.radio("Inventario", ["Sumar al inventario", "Reemplazar inventario", "No modificar inventario"], index=0)
        with c2:
            costo = st.number_input("Nuevo costo", value=float(costo0), step=10.0, min_value=0.0)
            venta = st.number_input("Nuevo precio venta", value=float(venta0), step=10.0, min_value=0.0)
            margen_obj = st.number_input("Margen objetivo %", value=35.0, step=1.0)
        if modo == "Sumar al inventario": nuevo_stock = stock_actual + cantidad_compra
        elif modo == "Reemplazar inventario": nuevo_stock = cantidad_compra
        else: nuevo_stock = stock_actual
        sugerido = precio_por_margen(costo, margen_obj)
        st.write(f"**Nuevo stock:** {nuevo_stock:g}")
        st.write(f"**Margen nuevo:** {margen(costo, venta):.1f}% | **Utilidad unidad:** ${money(venta-costo):,}".replace(',', '.'))
        st.write(f"**Precio sugerido con margen {margen_obj:.0f}%:** ${money(sugerido):,}".replace(',', '.'))
        usar_sugerido = st.checkbox("Usar precio sugerido al guardar")
        departamento = st.text_input("Departamento", value=depto0)
        submitted = st.form_submit_button("Guardar producto escaneado", type="primary")
        if submitted:
            venta_final = sugerido if usar_sugerido else venta
            item = {
                "codigo": codigo,
                "descripcion": descripcion,
                "cantidad_compra": cantidad_compra,
                "stock_final": nuevo_stock,
                "costo": costo,
                "precio_venta": venta_final,
                "margen": margen(costo, venta_final),
                "departamento": departamento,
                "nuevo_producto": "SI" if existing is None else "NO",
            }
            # replace if already scanned
            st.session_state.items = [x for x in st.session_state.items if x["codigo"] != codigo]
            st.session_state.items.append(item)
            try:
                del st.query_params["barcode"]
            except Exception:
                pass
            st.success("Producto guardado.")
            st.rerun()

# ---------- Export ----------
st.subheader("4) Productos listos para importar")
if st.session_state.items:
    out = pd.DataFrame(st.session_state.items)
    st.dataframe(out, use_container_width=True)
    # Excel compatible: keep simple and also keep app audit cols
    export = out.rename(columns={
        "codigo":"Código",
        "descripcion":"Descripción",
        "stock_final":"Cantidad en inventario",
        "costo":"Precio de costo",
        "precio_venta":"Precio de venta",
        "departamento":"Departamento",
    })[["Código","Descripción","Precio de costo","Precio de venta","Departamento","Cantidad en inventario","nuevo_producto","cantidad_compra","margen"]]
    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        export.to_excel(writer, index=False, sheet_name="Importar Eleventa")
    st.download_button(
        "⬇️ Descargar Excel para Eleventa",
        data=bio.getvalue(),
        file_name=f"eleventa_importacion_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    if st.button("Limpiar lista"):
        st.session_state.items = []
        st.rerun()
else:
    st.caption("Aún no hay productos guardados.")

st.divider()
st.caption("Consejo: si el plástico está arrugado o con reflejo, estira el envase o escanea el código desde una zona más plana. Los lectores fallan aunque el ojo humano vea los números.")
