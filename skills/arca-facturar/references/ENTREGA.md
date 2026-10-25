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

Para varios contactos, `recipients` recibe una lista de objetos `name`/`email`, todos
en Para. Se valida cada dirección, se rechazan duplicados e inyección de encabezados
y no se combina con `recipient_name`/`recipient_email`. `subject` y `body` opcionales
permiten conservar texto exacto aprobado. Todos los destinatarios, remitente, texto
y bytes del adjunto quedan vinculados a la misma propuesta y su confirmación.

El host debe disponer del plugin/conector Gmail con permiso de envío y adjuntos.
Verificar la cuenta con la herramienta de perfil y pasarla como `sender_email` al
preparar. Mostrar siempre una propuesta completa: cuenta remitente, todos los
destinatarios en Para, CC y CCO (escribir «ninguno» si están vacíos), asunto, cuerpo
íntegro y PDF definitivo adjunto con nombre, ruta, enlace local y hash verificado.
No reemplazar el cuerpo por un resumen ni dar por revisado un adjunto solo por su nombre.

La API actual `recipients` representa Para; no admite CC/CCO no vacíos. Si se piden,
no agregarlos a mano al JSON después de `invoice_email_handoff`: ampliar primero la
preparación y su vínculo de integridad o explicar la limitación y detener el envío.

Después de mostrar la propuesta, ofrecer verla también como borrador en Gmail.
Crear el borrador remoto solo si el usuario acepta esa oferta y el conector lo permite.
Antes de crearlo, usar `invoice_email_handoff` para comprobar el mensaje y los bytes
del PDF (sin `reserve_send`, con `ready_for_review=true`), y entregar al conector el mismo MIME validado. El token de confirmación
valida integridad; no equivale por sí mismo a consentimiento humano para enviar.
Mostrar el ID/enlace del borrador devuelto por Gmail. Una respuesta afirmativa a
«¿Querés verlo como borrador?» autoriza crear ese borrador, nunca enviarlo.

Para futuras facturas, esperar siempre aprobación explícita del correo **posterior
a mostrar la propuesta**. No aceptar como sustituto una autorización anterior a esa
visualización. La aprobación de la factura, el CAE, el archivo, un borrador o una
factura anterior no autoriza este envío. Si ya se recibió esa aprobación posterior
para la propuesta exacta mostrada, continuar sin pedirla otra vez.

Si cambian remitente, Para/CC/CCO, asunto, cuerpo o adjunto (nombre o bytes), volver
a preparar y mostrar todos los datos y esperar una nueva aprobación antes de enviar.
Si se creó un borrador Gmail, releer su mensaje y adjunto mediante los identificadores
devueltos por el conector y contrastarlos con la propuesta aprobada. No enviar a ciegas
un borrador que pudo editarse en Gmail. Si no se puede comprobar el contenido o el PDF,
explicar el bloqueo y no enviar. Las modificaciones externas requieren la misma revisión.

Antes de un nuevo envío, consultar `invoice_email_status`. `not_reserved` no prueba
que nunca se envió: contemplar la información del usuario, mensajes manuales o previos
a este registro y consultar Enviados. Si ya se envió, recuperar su ID sin reenviar.

Después de la aprobación, revalidar con `invoice_email_handoff`, confirmation exacta
y `reserve_send=true` el mensaje y PDF de la última propuesta. Solo
`ready_for_gmail=true` autoriza al host a realizar el único intento reservado;
`attempt_id` identifica ese intento. En modo revisión el campo es falso y no se
reserva nada, por lo que ver un borrador no consume un intento ni permite enviarlo.
Enviar una sola vez mediante el conector, directamente o desde el borrador aprobado.
El MCP registra primero `sending` en un archivo privado por factura, con bloqueo
entre procesos: otra propuesta de esa misma factura tampoco evita el bloqueo.

Registrar con `record_invoice_email_result` el `proposal_id`, `attempt_id` y resultado:
`sent` requiere el ID exacto confirmado por Gmail; ante timeout o resultado ambiguo
usar `uncertain`. Si una interrupción impide registrar el resultado, `sending` sigue
bloqueado. Consultar Enviados sin reenviar; si se encuentra, registrar `sent` con su ID.
Si no se encuentra, mantener el bloqueo y explicar que hace falta revisión manual.
No hay liberación automática ni sustitución de un ID confirmado; un reenvío adicional
no está habilitado por este circuito. El registro de entrega no modifica el diario
fiscal y no sustituye la aprobación humana ni la confirmación real del proveedor.
No confundir `sent: false` con una respuesta de Gmail; exigir confirmación del proveedor.

Si el host no tiene Gmail, no pedir claves o tokens por chat: guiar su conexión por
la interfaz del host o entregar el .eml para envío manual. No usar SMTP improvisado,
no mover certificados al Drive y no publicar el PDF como sustituto del adjunto.
