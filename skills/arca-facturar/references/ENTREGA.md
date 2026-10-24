# Guardado y Gmail

El sufijo sigue el esquema CUIT_TIPO_PUNTO_NUMERO de ARCA. El prefijo usa el
período del servicio: por ejemplo `2026-09 - sep - 20123456786_211_00002_00000008.pdf`.
Períodos parciales o de varios meses usan desde/hasta. `filing_period` permite
AAAA-MM, AAAA-MM-DD o AAAA-MM-DD_al_AAAA-MM-DD, sin modificar fechas fiscales.
El identificador/hash del borrador se conserva en el diario, no hace falta en el nombre.

`export_invoice_pdf` genera la copia privada con nombre descriptivo.
`invoice_output_options` propone período y carpeta. Después de mostrarlos y obtener
aprobación, `save_invoice_pdf` copia a esa carpeta y opcionalmente la recuerda en
`delivery-preferences.json` del perfil privado. La carpeta debe existir y estar
fuera de repositorios Git y de las credenciales. No se sobreescriben archivos distintos.
Se conserva el PDF privado y un recibo local con hash, sin cambiar el diario fiscal.
Los renderizados se escriben separados del archivo guardado. La carpeta privada
predeterminada se puede crear al guardar con aprobación; las carpetas externas
deben existir, para no crear una ruta equivocada si falta una unidad sincronizada.
Guardar en una unidad sincronizada no acredita que Drive ya haya subido el archivo.

`prepare_invoice_email` solo admite comprobantes de producción autorizados y requiere
contacto y correo explícitos. Devuelve el texto, adjunto y sus hashes; crea una propuesta
JSON compatible con las herramientas Gmail del host y un .eml privado. No conecta a
Google ni envía, ni crea borradores en la cuenta. Nunca contiene credenciales fiscales.
Se congela una copia del PDF para esa propuesta; reexportar no cambia su adjunto.
El cuerpo usa descripción, número, período e importe y, si consta en el registro,
indica la fecha del pago recibido. No inventar pagos ni pedir de nuevo un importe cobrado.

El host debe disponer del plugin/conector Gmail con permiso de envío y adjuntos.
Verificar la cuenta con la herramienta de perfil; mostrar esa cuenta junto con el
destinatario, asunto, cuerpo y adjunto y obtener aprobación antes de crear/enviar correo.
Pasar esa cuenta como `sender_email` al preparar y usar `invoice_email_handoff`
con la confirmation exacta antes de entregar el JSON al conector. Esa comprobación
rechaza cambios del mensaje o adjunto y no es prueba de aprobación humana ni envío.
Si el usuario aprueba una propuesta sin cambios, no volver a pedir lo mismo.
Si cambia cuenta, destinatario, texto o adjunto, presentar la nueva propuesta.
No confundir `sent: false` con una respuesta de Gmail; exigir confirmación del proveedor.

Si el host no tiene Gmail, no pedir claves o tokens por chat: guiar su conexión por
la interfaz del host o entregar el .eml para envío manual. No usar SMTP improvisado,
no mover certificados al Drive y no publicar el PDF como sustituto del adjunto.
