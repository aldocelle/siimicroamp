# SII Hub · Microverse

MVP de gestión documental tributaria para Chile. **No es un servicio oficial del SII** y no consulta, valida ni envía documentos al SII en esta versión.

## Incluye
- Registro e inicio de sesión con cookies HttpOnly y protección CSRF.
- Organizaciones separadas por usuario, con aislamiento de documentos por organización.
- Importación de XML DTE (un DTE o `EnvioDTE` con varios documentos).
- Extracción de emisor, receptor, RUT, tipo DTE, folio, fecha y montos.
- Validación **local** de dígito verificador RUT, coherencia básica de montos y duplicados.
- Bandeja de documentos con búsqueda, revisión/edición, descarga del XML original y exportación CSV.
- Auditoría básica de correcciones y vista de conectores oficiales pendientes.

## Ejecutar localmente

Requiere Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Abre http://127.0.0.1:8000. Crea una cuenta con contraseña de al menos 10 caracteres.

Los datos se guardan en `data/sii_hub.sqlite3` (configurable con `SII_DB_PATH`). No subas ese archivo a GitHub. Para producción establece `SII_COOKIE_SECURE=1` y sirve la aplicación por HTTPS.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## Docker (solo local)

```bash
docker compose up --build
```

Abre http://127.0.0.1:8000. El volumen Docker conserva la base de datos entre reinicios. La configuración publicada enlaza el puerto únicamente a localhost y **no incluye HTTPS**.

## Límites del MVP

- Solo se importa **XML DTE**. OCR, PDF e imágenes aún no están implementados.
- Los estados «extraído» y «revisado» son **internos**. Ningún documento se declara «verificado por SII».
- No hay autenticación con certificado, consultas DTE, emisión, envío, aceptación/reclamo ni descarga automática del Registro de Compras y Ventas.
- El CSV exportado es **un registro interno**, no un archivo garantizado para carga al RCV.
- SQLite y la autenticación de esta versión están pensadas para pruebas/local. Antes de producción faltan PostgreSQL, migraciones, backups, protección anti-fuerza-bruta, roles multiusuario, límites de uso, observabilidad y una revisión de seguridad.
- La importación XML usa `defusedxml` para bloquear entidades peligrosas; el tamaño máximo es 10 MB por archivo.
- No introducir certificados privados, claves ni tokens en el repositorio.

## Fuentes oficiales para conectores futuros

- [Servicios web DTE para contribuyentes autorizados](https://www.sii.cl/factura_electronica/doc_emp_aut.htm)
- [Documentación de boleta electrónica](https://www.sii.cl/servicios_online/3811-3841.html)
- [Registro de Compras y Ventas](https://www.sii.cl/servicios_online/1039-guias_ayuda-3256.html)

Los futuros conectores deben tener ambientes de certificación y producción separados, credenciales cifradas en backend y confirmación explícita para operaciones tributarias con efectos externos.
