# Hoja de ruta técnica · SII Hub

## MVP v0.1 (código en repositorio)
- Registro, login y sesiones HttpOnly + CSRF.
- Aislamiento de documentos por organización.
- XML DTE, EnvioDTE y validación local de RUT y totales.
- Detección de duplicados, revisión, auditoría y exportación CSV interna.
- UI responsive y documentación de conectores pendientes.
- Tests automatizados en GitHub Actions (requieren ejecución de CI para confirmar resultados).

## v0.2: operación y seguridad
- PostgreSQL + migraciones, roles multiusuario, backups, recuperación.
- Protección contra fuerza bruta, límites por organización, colas y almacenamiento privado.
- Parser XML con validación XSD oficial, controles adicionales y tests de casos reales anonimizados.
- Soporte de PDF con texto y OCR mediante proveedor explícito; revisión obligatoria.
- CSV específico para operaciones RCV según esquemas oficiales, con pruebas de formato.

## v0.3: conector SII autorizado
- Adaptador SOAP de semilla, firma XML con certificado digital y token.
- Ambientes de certificación y producción separados.
- QueryEstDte y QueryEstUp con auditoría de peticiones y respuestas.
- Custodia cifrada de certificados, rotación y revocación.
- Pruebas contra certificación SII con contribuyente autorizado.
- Nunca cambiar el estado de un documento a «verificado SII» sin respuesta real.

## v0.4: procesos tributarios avanzados
- Aceptación y reclamo DTE con confirmación expresa y permisos.
- Boleta electrónica REST y emisión DTE solo tras habilitación y certificación.
- CAF, firma, TED, envío y seguimiento, con revisión de cumplimiento.
- Integración opcional con API de terceros, claramente separada del conector SII.

Referencias:
- https://www.sii.cl/factura_electronica/doc_emp_aut.htm
- https://www.sii.cl/servicios_online/3811-3841.html
- https://www.sii.cl/servicios_online/1039-guias_ayuda-3256.html
