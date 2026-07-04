# La Despensa Scanner Eleventa

App web para celular: escanea código de barras, busca productos desde un Excel de Eleventa, permite actualizar cantidad, costo y precio de venta, y genera un Excel para importar en Eleventa.

## Subir a Streamlit Cloud desde celular

1. Crea un repositorio en GitHub.
2. Sube estos archivos al repositorio:
   - app.py
   - requirements.txt
3. Entra a https://share.streamlit.io/
4. Selecciona el repositorio.
5. Main file path: `app.py`
6. Deploy.

## Uso

1. Abre la app en el celular.
2. Sube el Excel exportado desde Eleventa.
3. Escanea el código con la cámara.
4. Edita cantidad comprada, costo y precio venta.
5. Guarda el producto.
6. Descarga el Excel final.

## Importante

Para que la cámara funcione en iPhone/Android, la app debe estar publicada en HTTPS, por ejemplo en Streamlit Cloud.
