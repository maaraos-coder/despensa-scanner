import base64
from io import BytesIO
from datetime import datetime

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="La Despensa Compras", page_icon="📦", layout="centered")

st.title("📦 La Despensa Compras")
st.caption("Escanea productos, suma inventario, actualiza costo/venta y genera Excel para Eleventa.")

# -------------------- Helpers --------------------
def normalize_col(c: str) -> str:
    return str(c).strip().lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")

def find_col(df, candidates):
    norm_map = {normalize_col(c): c for c in df.columns}
    for cand in candidates:
        cand_n = normalize_col(cand)
        for n, original in norm_map.items():
            if cand_n == n or cand_n in n:
                return original
    return None

def money(x):
    try:
        return f"${float(x):,.0f}".replace(",", ".")
    except Exception:
        return "$0"

def to_float(x, default=0.0):
    try:
        if pd.isna(x):
            return default
        if isinstance(x, str):
            x = x.replace("$", "").replace(".", "").replace(",", ".").strip()
        return float(x)
    except Exception:
        return default

def calc_margin(cost, price):
    cost = to_float(cost)
    price = to_float(price)
    if price <= 0:
        return 0.0
    return (price - cost) / price * 100

def calc_markup(cost, price):
    cost = to_float(cost)
    price = to_float(price)
    if cost <= 0:
        return 0.0
    return (price - cost) / cost * 100

def price_for_margin(cost, margin_percent):
    cost = to_float(cost)
    m = to_float(margin_percent) / 100
    if m >= 1:
        return cost
    return round(cost / (1 - m), 0)

# -------------------- Session state --------------------
if "items" not in st.session_state:
    st.session_state.items = []
if "catalog" not in st.session_state:
    st.session_state.catalog = None
if "cols" not in st.session_state:
    st.session_state.cols = {}

# -------------------- Read barcode from query params --------------------
barcode_from_url = None
try:
    barcode_from_url = st.query_params.get("barcode")
except Exception:
    barcode_from_url = None

if barcode_from_url:
    st.success(f"✅ Código leído: {barcode_from_url}")
    st.session_state.last_barcode = str(barcode_from_url)
    # Clear query param so it doesn't keep reprocessing on every rerun
    try:
        st.query_params.clear()
    except Exception:
        pass

# -------------------- Sidebar / catalog upload --------------------
with st.expander("1) Cargar catálogo/exportación de Eleventa", expanded=True):
    uploaded = st.file_uploader("Sube el Excel exportado desde Eleventa", type=["xlsx", "xls"])
    if uploaded:
        try:
            df = pd.read_excel(uploaded)
            df.columns = [str(c).strip() for c in df.columns]
            st.session_state.catalog = df
            cols = {
                "code": find_col(df, ["codigo", "codigo de barras", "código", "clave", "sku"]),
                "desc": find_col(df, ["descripcion", "descripción", "producto", "nombre"]),
                "stock": find_col(df, ["existencia", "inventario", "hay", "cantidad", "stock"]),
                "cost": find_col(df, ["costo", "precio costo", "precio de costo"]),
                "price": find_col(df, ["precio venta", "precio de venta", "venta", "precio"]),
                "mayoreo": find_col(df, ["mayoreo", "precio mayoreo", "precio de mayoreo"]),
                "dept": find_col(df, ["departamento", "categoria", "categoría"]),
                "type": find_col(df, ["tipo de venta", "tipo", "se vende"]),
                "min": find_col(df, ["minimo", "mínimo", "inventario minimo"]),
                "max": find_col(df, ["maximo", "máximo", "inventario maximo"]),
            }
            st.session_state.cols = cols
            st.success(f"Catálogo cargado: {len(df)} productos")
            with st.expander("Columnas detectadas"):
                st.json(cols)
        except Exception as e:
            st.error(f"No pude leer el Excel: {e}")
    else:
        st.info("Puedes probar el escáner sin catálogo, pero para sumar inventario debes cargar el Excel de Eleventa.")

# -------------------- Scanner HTML --------------------
st.subheader("2) Escanear código de barras")
st.write("Presiona iniciar cámara. Al detectar el código, se detiene la cámara y aparece el mensaje de código leído.")

scanner_html = r"""
<div style="font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;">
  <div id="reader" style="width:100%; min-height:320px; background:#000; border-radius:18px; overflow:hidden; display:flex; align-items:center; justify-content:center; color:white; font-size:26px; font-weight:700; text-align:center;">
    Presiona iniciar cámara
  </div>
  <div style="display:flex; gap:10px; margin-top:12px;">
    <button id="startBtn" style="flex:1; padding:18px; border-radius:16px; border:0; background:#111; color:#fff; font-size:22px; font-weight:800;">📷 Iniciar cámara</button>
    <button id="stopBtn" style="flex:1; padding:18px; border-radius:16px; border:1px solid #888; background:#fff; color:#111; font-size:22px; font-weight:800;">Detener</button>
  </div>
  <div id="status" style="margin-top:12px; background:#f1f1f1; padding:14px; border-radius:14px; font-size:18px; color:#222;">Estado: esperando inicio.</div>
  <div style="font-size:14px; color:#666; margin-top:8px; line-height:1.35;">
    Consejo iPhone: usa buena luz, limpia la cámara, acerca y aleja lentamente hasta que enfoque. Prueba Safari si Chrome falla.
  </div>
</div>

<script src="https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js"></script>
<script>
let html5QrCode = null;
let scanning = false;
let lastCode = null;
const statusEl = document.getElementById("status");
const readerEl = document.getElementById("reader");

function setStatus(msg) { statusEl.innerText = "Estado: " + msg; }

async function stopCamera() {
  if (html5QrCode && scanning) {
    try { await html5QrCode.stop(); } catch(e) {}
    try { await html5QrCode.clear(); } catch(e) {}
    scanning = false;
  }
}

function sendBarcode(code) {
  const clean = String(code || '').trim();
  if (!clean || clean === lastCode) return;
  lastCode = clean;
  setStatus("✅ Código leído: " + clean + ". Cargando producto...");
  readerEl.innerHTML = "✅ Código leído<br>" + clean;
  setTimeout(() => {
    const url = new URL(window.parent.location.href);
    url.searchParams.set("barcode", clean);
    window.parent.location.href = url.toString();
  }, 450);
}

async function startCamera() {
  setStatus("solicitando permiso de cámara...");
  readerEl.innerText = "Abriendo cámara...";

  if (!window.Html5Qrcode) {
    setStatus("no cargó la librería del escáner. Recarga la página.");
    return;
  }

  try {
    await stopCamera();
    html5QrCode = new Html5Qrcode("reader", { verbose: false });
    const config = {
      fps: 12,
      qrbox: { width: 300, height: 170 },
      aspectRatio: 1.777,
      disableFlip: true,
      experimentalFeatures: { useBarCodeDetectorIfSupported: true },
      videoConstraints: {
        facingMode: { ideal: "environment" },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
        focusMode: "continuous"
      }
    };
    scanning = true;
    await html5QrCode.start(
      { facingMode: { ideal: "environment" } },
      config,
      async (decodedText, decodedResult) => {
        await stopCamera();
        sendBarcode(decodedText);
      },
      (errorMessage) => {
        // keep quiet; repeated no-detection messages are normal
      }
    );
    setStatus("cámara activa. Apunta al código de barras.");
  } catch (err) {
    scanning = false;
    readerEl.innerText = "No se pudo abrir la cámara";
    setStatus("error: " + (err && err.message ? err.message : err));
  }
}

document.getElementById("startBtn").addEventListener("click", startCamera);
document.getElementById("stopBtn").addEventListener("click", async () => {
  await stopCamera();
  readerEl.innerText = "Cámara detenida";
  setStatus("cámara detenida.");
});
</script>
"""
components.html(scanner_html, height=560, scrolling=False)

manual_code = st.text_input("O ingresa el código manualmente", value=st.session_state.get("last_barcode", ""))
if st.button("Usar código", type="primary"):
    if manual_code.strip():
        st.session_state.last_barcode = manual_code.strip()
    else:
        st.warning("Ingresa o escanea un código.")

code = st.session_state.get("last_barcode", "").strip()

# -------------------- Product form --------------------
if code:
    st.subheader("3) Actualizar producto")
    df = st.session_state.catalog
    cols = st.session_state.cols
    existing = None
    if df is not None and cols.get("code"):
        s = df[cols["code"]].astype(str).str.strip()
        matches = df[s == code]
        if not matches.empty:
            existing = matches.iloc[0]

    if existing is not None:
        desc = str(existing.get(cols.get("desc"), "")) if cols.get("desc") else ""
        stock = to_float(existing.get(cols.get("stock"), 0)) if cols.get("stock") else 0
        cost = to_float(existing.get(cols.get("cost"), 0)) if cols.get("cost") else 0
        price = to_float(existing.get(cols.get("price"), 0)) if cols.get("price") else 0
        mayoreo = to_float(existing.get(cols.get("mayoreo"), 0)) if cols.get("mayoreo") else 0
        dept = str(existing.get(cols.get("dept"), "")) if cols.get("dept") else ""
        st.success("Producto encontrado")
    else:
        desc, stock, cost, price, mayoreo, dept = "", 0, 0, 0, 0, ""
        st.warning("Código no encontrado. Puedes agregarlo como producto nuevo.")

    with st.form("product_form", clear_on_submit=False):
        st.text_input("Código", value=code, disabled=True)
        desc_new = st.text_input("Descripción", value=desc, max_chars=60)
        dept_new = st.text_input("Departamento", value=dept)

        c1, c2 = st.columns(2)
        with c1:
            st.metric("Stock actual", f"{stock:g}")
            qty_buy = st.number_input("Cantidad comprada", min_value=0.0, step=1.0, value=1.0)
        with c2:
            mode = st.radio("Inventario", ["Sumar al inventario", "Reemplazar inventario", "No modificar inventario"], index=0)
            if mode == "Sumar al inventario":
                final_stock = stock + qty_buy
            elif mode == "Reemplazar inventario":
                final_stock = qty_buy
            else:
                final_stock = stock
            st.metric("Nuevo stock", f"{final_stock:g}")

        c3, c4 = st.columns(2)
        with c3:
            new_cost = st.number_input("Nuevo costo", min_value=0.0, step=10.0, value=float(cost))
        with c4:
            new_price = st.number_input("Nuevo precio venta", min_value=0.0, step=10.0, value=float(price))

        margin_now = calc_margin(cost, price)
        margin_new = calc_margin(new_cost, new_price)
        markup_new = calc_markup(new_cost, new_price)
        profit = new_price - new_cost

        c5, c6, c7 = st.columns(3)
        c5.metric("Margen actual", f"{margin_now:.1f}%")
        c6.metric("Margen nuevo", f"{margin_new:.1f}%")
        c7.metric("Utilidad unidad", money(profit))
        st.caption(f"Markup nuevo sobre costo: {markup_new:.1f}%")

        target = st.selectbox("Precio sugerido por margen objetivo", ["No aplicar", "25%", "30%", "35%", "40%", "45%", "50%"])
        if target != "No aplicar":
            target_m = float(target.replace("%", ""))
            suggested = price_for_margin(new_cost, target_m)
            st.info(f"Precio sugerido para margen {target}: {money(suggested)}")

        new_mayoreo = st.number_input("Precio mayoreo opcional", min_value=0.0, step=10.0, value=float(mayoreo))
        is_new = existing is None
        submitted = st.form_submit_button("Guardar producto escaneado", type="primary")

        if submitted:
            item = {
                "codigo": code,
                "descripcion": desc_new,
                "departamento": dept_new,
                "cantidad_inventario": final_stock,
                "cantidad_comprada": qty_buy,
                "precio_costo": new_cost,
                "precio_venta": new_price,
                "precio_mayoreo": new_mayoreo,
                "margen": round(margin_new, 2),
                "utilidad": profit,
                "producto_nuevo": "SI" if is_new else "NO",
                "fecha": datetime.now().strftime("%Y-%m-%d %H:%M"),
            }
            # Replace if code already saved
            st.session_state.items = [x for x in st.session_state.items if x["codigo"] != code]
            st.session_state.items.append(item)
            st.success("Producto guardado en la lista de importación.")
            st.session_state.last_barcode = ""

# -------------------- Export --------------------
st.subheader("4) Productos para importar")
if st.session_state.items:
    out_df = pd.DataFrame(st.session_state.items)
    st.dataframe(out_df, use_container_width=True)

    export_df = out_df.copy()
    # Simple Eleventa-friendly names; user can map columns during import
    export_df = export_df.rename(columns={
        "codigo": "Codigo",
        "descripcion": "Descripcion",
        "precio_costo": "Precio Costo",
        "precio_venta": "Precio Venta",
        "precio_mayoreo": "Precio Mayoreo",
        "departamento": "Departamento",
        "cantidad_inventario": "Cantidad Inventario",
    })
    export_cols = ["Codigo", "Descripcion", "Precio Costo", "Precio Venta", "Precio Mayoreo", "Departamento", "Cantidad Inventario"]
    export_df = export_df[[c for c in export_cols if c in export_df.columns]]

    bio = BytesIO()
    with pd.ExcelWriter(bio, engine="xlsxwriter") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Importar Eleventa")
        audit = out_df.copy()
        audit.to_excel(writer, index=False, sheet_name="Detalle Compra")
        workbook = writer.book
        money_fmt = workbook.add_format({'num_format': '$#,##0'})
        pct_fmt = workbook.add_format({'num_format': '0.0%'})
        for sheet_name in ["Importar Eleventa", "Detalle Compra"]:
            ws = writer.sheets[sheet_name]
            ws.set_column(0, 0, 18)
            ws.set_column(1, 1, 32)
            ws.set_column(2, 6, 16, money_fmt)
    bio.seek(0)

    st.download_button(
        "📥 Descargar Excel para Eleventa",
        data=bio,
        file_name=f"eleventa_importacion_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )

    if st.button("Vaciar lista"):
        st.session_state.items = []
        st.rerun()
else:
    st.info("Aún no hay productos guardados.")

st.divider()
st.caption("En Eleventa, al importar, marca actualizar productos existentes y actualizar inventario solo si quieres reemplazar/sumar según el archivo generado.")
