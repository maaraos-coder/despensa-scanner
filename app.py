import io
import math
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image, ImageOps, ImageEnhance

try:
    import cv2
except Exception:
    cv2 = None

try:
    import zxingcpp
except Exception:
    zxingcpp = None

st.set_page_config(page_title="La Despensa Compras", page_icon="📦", layout="wide")

st.markdown(
    """
    <style>
    .main .block-container { padding-top: 1rem; max-width: 900px; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
    .okbox {background:#e8f7ed;border:1px solid #a7e0b8;border-radius:12px;padding:12px;margin:8px 0;}
    .warnbox {background:#fff4df;border:1px solid #ffd18a;border-radius:12px;padding:12px;margin:8px 0;}
    .badbox {background:#fdeaea;border:1px solid #ffb3b3;border-radius:12px;padding:12px;margin:8px 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------- Helpers -----------------------------

def normalize_col(name: str) -> str:
    return str(name).strip().lower().replace("á", "a").replace("é", "e").replace("í", "i").replace("ó", "o").replace("ú", "u").replace("ñ", "n")

CANDIDATES = {
    "codigo": ["codigo", "codigo de barras", "codigo producto", "codigo del producto", "cod", "barcode", "ean", "upc"],
    "descripcion": ["descripcion", "descripcion del producto", "producto", "nombre", "articulo", "description"],
    "costo": ["precio de costo", "costo", "precio costo", "cost", "ultimo costo"],
    "venta": ["precio de venta", "precio venta", "venta", "precio", "price"],
    "mayoreo": ["precio mayoreo", "precio de mayoreo", "mayoreo"],
    "inventario": ["cantidad", "inventario", "existencia", "existencia actual", "hay", "stock"],
    "departamento": ["departamento", "categoria", "category"],
    "tipo_venta": ["tipo de venta", "se vende", "unidad", "tipo"],
    "minimo": ["minimo", "inventario minimo", "inv. minimo"],
    "maximo": ["maximo", "inventario maximo", "inv. maximo"],
}

def guess_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    norm_map = {normalize_col(c): c for c in df.columns}
    out = {}
    for key, names in CANDIDATES.items():
        out[key] = None
        for n in names:
            if n in norm_map:
                out[key] = norm_map[n]
                break
        if out[key] is None:
            for norm, original in norm_map.items():
                if any(n in norm for n in names):
                    out[key] = original
                    break
    return out

def clean_code(x) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return "".join(ch for ch in s if ch.isdigit()) or s

def to_float(x, default=0.0) -> float:
    try:
        if pd.isna(x):
            return default
        if isinstance(x, str):
            x = x.replace("$", "").replace(".", "").replace(",", ".").strip()
        return float(x)
    except Exception:
        return default

def money(x: float) -> str:
    try:
        return f"${int(round(float(x))):,}".replace(",", ".")
    except Exception:
        return "$0"

def margin(cost: float, price: float) -> float:
    if price <= 0:
        return 0.0
    return (price - cost) / price * 100

def markup(cost: float, price: float) -> float:
    if cost <= 0:
        return 0.0
    return (price - cost) / cost * 100

def price_from_margin(cost: float, target_margin: float) -> float:
    if target_margin >= 99.0:
        return cost
    return cost / (1 - target_margin / 100.0)

# ---------------------- Barcode decoding ----------------------

def pil_to_cv(img: Image.Image):
    arr = np.array(img.convert("RGB"))
    if cv2 is None:
        return arr
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

def try_decode_zxing(pil_img: Image.Image) -> List[str]:
    if zxingcpp is None:
        return []
    found = []
    variants = []
    base = ImageOps.exif_transpose(pil_img).convert("RGB")
    variants.append(base)
    # Mejoras útiles para iPhone: contraste, escala, gris, recortes y rotaciones
    variants.append(ImageEnhance.Contrast(base).enhance(1.8))
    variants.append(ImageEnhance.Sharpness(base).enhance(2.0))
    w, h = base.size
    crop_boxes = [
        (0, int(h*0.20), w, int(h*0.80)),
        (int(w*0.05), int(h*0.25), int(w*0.95), int(h*0.75)),
        (int(w*0.10), int(h*0.30), int(w*0.90), int(h*0.70)),
        (0, 0, w, h),
    ]
    for box in crop_boxes:
        try:
            variants.append(base.crop(box))
        except Exception:
            pass
    # Rotaciones pequeñas ayudan si el usuario inclina el celular
    for angle in [-8, -4, 4, 8, 90, -90]:
        try:
            variants.append(base.rotate(angle, expand=True, fillcolor="white"))
        except Exception:
            pass

    for v in variants:
        try:
            # agrandar si es muy chico
            if max(v.size) < 1600:
                scale = 1600 / max(v.size)
                v = v.resize((int(v.width * scale), int(v.height * scale)))
            results = zxingcpp.read_barcodes(v)
            for r in results:
                txt = str(r.text).strip()
                if txt and txt not in found:
                    found.append(txt)
        except Exception:
            continue
    return found

def try_decode_cv(pil_img: Image.Image) -> List[str]:
    if cv2 is None:
        return []
    try:
        img = pil_to_cv(pil_img)
        detector = cv2.barcode.BarcodeDetector()
        ok, decoded_info, _, _ = detector.detectAndDecodeMulti(img)
        out = []
        if ok:
            for txt in decoded_info:
                if txt and txt not in out:
                    out.append(txt)
        return out
    except Exception:
        return []

def decode_barcode(image_bytes: bytes) -> Tuple[List[str], Image.Image]:
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    codes = []
    for c in try_decode_zxing(img) + try_decode_cv(img):
        cc = clean_code(c)
        if cc and cc not in codes:
            codes.append(cc)
    return codes, img

# ---------------------- State ----------------------
if "catalog" not in st.session_state:
    st.session_state.catalog = None
if "cols" not in st.session_state:
    st.session_state.cols = {}
if "cart" not in st.session_state:
    st.session_state.cart = []
if "last_code" not in st.session_state:
    st.session_state.last_code = ""

st.title("📦 La Despensa Compras")
st.caption("Escanea productos, suma inventario, actualiza costo/precio y genera Excel para importar en Eleventa.")

# ---------------------- Catalog upload ----------------------
with st.expander("1) Cargar catálogo exportado desde Eleventa", expanded=st.session_state.catalog is None):
    cat_file = st.file_uploader("Sube el Excel de productos exportado desde Eleventa", type=["xlsx", "xls", "csv"])
    if cat_file is not None:
        try:
            if cat_file.name.lower().endswith(".csv"):
                df = pd.read_csv(cat_file, dtype=str)
            else:
                df = pd.read_excel(cat_file, dtype=str)
            df.columns = [str(c).strip() for c in df.columns]
            cols = guess_columns(df)
            if not cols.get("codigo"):
                st.error("No pude identificar la columna de código. Selecciónala abajo.")
            st.session_state.catalog = df
            st.session_state.cols = cols
            st.success(f"Catálogo cargado: {len(df)} productos.")
        except Exception as e:
            st.error(f"No pude leer el archivo: {e}")

if st.session_state.catalog is not None:
    df = st.session_state.catalog
    cols = st.session_state.cols
    with st.expander("Columnas detectadas / corregir si es necesario", expanded=False):
        all_cols = [None] + list(df.columns)
        for key in ["codigo", "descripcion", "inventario", "costo", "venta", "mayoreo", "departamento", "tipo_venta", "minimo", "maximo"]:
            current = cols.get(key)
            idx = all_cols.index(current) if current in all_cols else 0
            cols[key] = st.selectbox(key, all_cols, index=idx, key=f"col_{key}")
        st.session_state.cols = cols

# ---------------------- Scanner ----------------------
st.header("2) Escanear o ingresar código")
st.markdown(
    """
    <div class="warnbox">
    <b>Modo recomendado en iPhone:</b> usa “Tomar foto del código”. Toca la pantalla del iPhone para enfocar,
    acerca el código hasta que ocupe gran parte de la imagen y evita reflejos en plástico arrugado.
    </div>
    """, unsafe_allow_html=True
)

scan_col1, scan_col2 = st.columns([1, 1])
with scan_col1:
    cam = st.camera_input("Tomar foto del código de barras", key="camera_barcode")
with scan_col2:
    uploaded_img = st.file_uploader("O sube una foto del código", type=["jpg", "jpeg", "png", "webp"], key="upload_barcode")

img_source = cam or uploaded_img
if img_source is not None:
    try:
        data = img_source.getvalue()
        codes, preview = decode_barcode(data)
        st.image(preview, caption="Imagen analizada", use_container_width=True)
        if codes:
            st.markdown(f"<div class='okbox'>✅ Código detectado: <b>{codes[0]}</b></div>", unsafe_allow_html=True)
            st.session_state.last_code = codes[0]
            if len(codes) > 1:
                st.info("También detecté: " + ", ".join(codes[1:]))
        else:
            st.markdown("<div class='badbox'>No pude leer el código en esta foto. Intenta con más luz, menos reflejo y el código más plano.</div>", unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Error leyendo la imagen: {e}")

manual = st.text_input("Código manual / último leído", value=st.session_state.last_code, placeholder="Ej: 7613034623042")
if st.button("Usar código", type="primary", use_container_width=True):
    st.session_state.last_code = clean_code(manual)

code = clean_code(st.session_state.last_code)

# ---------------------- Product form ----------------------
def find_product(code: str):
    df = st.session_state.catalog
    cols = st.session_state.cols
    if df is None or not code or not cols.get("codigo"):
        return None
    tmp = df.copy()
    tmp["__code__"] = tmp[cols["codigo"]].apply(clean_code)
    matches = tmp[tmp["__code__"] == code]
    if matches.empty:
        return None
    return matches.iloc[0]

if code:
    st.header("3) Producto y actualización")
    row = find_product(code)
    is_new = row is None
    cols = st.session_state.cols

    if is_new:
        st.warning(f"Código no encontrado en el catálogo: {code}. Puedes crearlo como producto nuevo.")
        desc_default = ""
        stock_default = 0.0
        cost_default = 0.0
        price_default = 0.0
        dept_default = ""
        wholesale_default = 0.0
        tipo_default = "Unidad"
    else:
        desc_default = str(row.get(cols.get("descripcion"), "")) if cols.get("descripcion") else ""
        stock_default = to_float(row.get(cols.get("inventario"), 0)) if cols.get("inventario") else 0.0
        cost_default = to_float(row.get(cols.get("costo"), 0)) if cols.get("costo") else 0.0
        price_default = to_float(row.get(cols.get("venta"), 0)) if cols.get("venta") else 0.0
        wholesale_default = to_float(row.get(cols.get("mayoreo"), 0)) if cols.get("mayoreo") else 0.0
        dept_default = str(row.get(cols.get("departamento"), "")) if cols.get("departamento") else ""
        tipo_default = str(row.get(cols.get("tipo_venta"), "Unidad")) if cols.get("tipo_venta") else "Unidad"
        st.success(f"Producto encontrado: {desc_default}")

    c1, c2, c3 = st.columns(3)
    c1.metric("Stock actual", f"{stock_default:g}")
    c2.metric("Costo actual", money(cost_default))
    c3.metric("Venta actual", money(price_default))
    st.caption(f"Margen actual: {margin(cost_default, price_default):.1f}% | Markup: {markup(cost_default, price_default):.1f}%")

    with st.form("product_form"):
        desc = st.text_input("Descripción", value=desc_default)
        qty = st.number_input("Cantidad comprada / recibida", min_value=0.0, step=1.0, value=1.0)
        mode = st.radio("Inventario", ["Sumar al inventario", "Reemplazar inventario", "No modificar inventario"], horizontal=False)
        new_cost = st.number_input("Nuevo costo", min_value=0.0, step=10.0, value=float(cost_default))
        target_margin = st.number_input("Margen objetivo %", min_value=0.0, max_value=95.0, step=1.0, value=round(margin(cost_default, price_default), 1) if price_default else 35.0)
        suggested = price_from_margin(new_cost, target_margin)
        st.info(f"Precio sugerido para margen {target_margin:.1f}%: {money(suggested)}")
        use_suggested = st.checkbox("Usar precio sugerido", value=False)
        new_price_default = suggested if use_suggested else price_default
        new_price = st.number_input("Nuevo precio venta", min_value=0.0, step=10.0, value=float(round(new_price_default)))
        new_wholesale = st.number_input("Precio mayoreo opcional", min_value=0.0, step=10.0, value=float(wholesale_default))
        dept = st.text_input("Departamento", value=dept_default)
        tipo = st.selectbox("Tipo de venta", ["Unidad", "Granel", "Paquete/Kit"], index=0)

        if mode == "Sumar al inventario":
            final_stock = stock_default + qty
        elif mode == "Reemplazar inventario":
            final_stock = qty
        else:
            final_stock = stock_default
        st.markdown(f"### Nuevo stock: {final_stock:g}")
        st.markdown(f"**Margen nuevo:** {margin(new_cost, new_price):.1f}% | **Utilidad:** {money(new_price - new_cost)} | **Markup:** {markup(new_cost, new_price):.1f}%")
        submitted = st.form_submit_button("Guardar producto escaneado", type="primary", use_container_width=True)

    if submitted:
        item = {
            "codigo": code,
            "descripcion": desc,
            "cantidad_comprada": qty,
            "stock_anterior": stock_default,
            "stock_final": final_stock,
            "costo": new_cost,
            "precio_venta": new_price,
            "precio_mayoreo": new_wholesale,
            "departamento": dept,
            "tipo_venta": tipo,
            "nuevo": is_new,
            "modo_inventario": mode,
            "margen": margin(new_cost, new_price),
            "utilidad": new_price - new_cost,
        }
        # reemplaza si ya estaba escaneado
        st.session_state.cart = [x for x in st.session_state.cart if x["codigo"] != code]
        st.session_state.cart.append(item)
        st.success("Producto guardado en la lista de importación.")
        st.session_state.last_code = ""

# ---------------------- Export ----------------------
st.header("4) Productos listos para importar")
cart = st.session_state.cart
if cart:
    cart_df = pd.DataFrame(cart)
    st.dataframe(cart_df, use_container_width=True, hide_index=True)
    if st.button("Vaciar lista", use_container_width=True):
        st.session_state.cart = []
        st.rerun()

    # Generar Excel con columnas similares a Eleventa si existen
    out_rows = []
    cols = st.session_state.cols
    for x in cart:
        row_out = {}
        # nombres detectados del catálogo; si no existen, usa nombres estándar
        mapping = {
            cols.get("codigo") or "Código": x["codigo"],
            cols.get("descripcion") or "Descripción": x["descripcion"],
            cols.get("inventario") or "Cantidad": x["stock_final"],
            cols.get("costo") or "Precio de costo": x["costo"],
            cols.get("venta") or "Precio de venta": x["precio_venta"],
            cols.get("mayoreo") or "Precio mayoreo": x["precio_mayoreo"],
            cols.get("departamento") or "Departamento": x["departamento"],
            cols.get("tipo_venta") or "Tipo de venta": x["tipo_venta"],
        }
        for k, v in mapping.items():
            if k is not None and str(k).strip():
                row_out[k] = v
        out_rows.append(row_out)
    export_df = pd.DataFrame(out_rows)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
        export_df.to_excel(writer, index=False, sheet_name="Importar_Eleventa")
        cart_df.to_excel(writer, index=False, sheet_name="Detalle_App")
    st.download_button(
        "⬇️ Descargar Excel para importar en Eleventa",
        data=bio.getvalue(),
        file_name=f"importacion_eleventa_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )
else:
    st.info("Aún no hay productos guardados.")

with st.expander("Consejos para que lea bien el código", expanded=False):
    st.write("""
    - En iPhone, toca la pantalla sobre el código para enfocar antes de tomar la foto.
    - Evita reflejos: inclina un poco el envase o aléjalo de luces directas.
    - Si el plástico está curvo o arrugado, estíralo con la mano.
    - El código debe ocupar buena parte de la foto y verse horizontal.
    - Si no lee, usa el código impreso bajo las barras y presiona “Usar código”.
    """)
