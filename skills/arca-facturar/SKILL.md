---
name: arca-facturar
description: Preparar y emitir facturas de monotributo argentino con el MCP local ARCA Codex. Usar para Factura C y Factura de Crédito Electrónica MiPyME C (211) de servicios en pesos, configuración de certificados, CAE, PDF y conciliación de envíos inciertos.
---

# ARCA: facturación de servicios

Usar exclusivamente las herramientas del MCP `arca`/ARCA Codex. Si no están
disponibles, explicar que hace falta instalar y conectar el servidor local. Consultar
[configuración](references/CONFIGURACION.md). No simular una emisión ni reemplazar
el MCP por automatización de la web de ARCA sin una solicitud adicional del usuario.

## Configurar

Ejecutar `configuration_status`. Guiar el alta por terminal local con
`arca-codex setup`. Nunca solicitar por chat claves privadas, contraseñas,
certificados completos o clave fiscal. El usuario ingresa su clave fiscal solo en
el portal oficial de ARCA. No leer PEM, keyring ni variables con secretos.
No generar certificados de producción ni habilitar producción por iniciativa propia.
No cambiar el perfil, CUIT, punto de venta o ambiente para eludir un error de ARCA.

Distinguir la carpeta de homologación de la de producción. Usar un punto de venta
habilitado para Web Services, diferente del de Comprobantes en Línea. No inferir el
próximo número de facturas adjuntas. Cada usuario conserva sus propias credenciales.

## Preparar

1. Confirmar que el alcance sea emisor monotributista, servicio, ARS, cantidad 1,
   receptor identificado por CUIT y sin otros tributos. No convertir operaciones
   fuera de alcance a C para hacerlas pasar.
2. Determinar el tipo exacto. **Factura C = 11. FCE MiPyME C = 211.** No sustituir
   una por la otra. Si el usuario aporta un PDF de ejemplo, leer su código.
3. Reunir receptor (CUIT, razón social, domicilio, condición IVA), importe bruto
   total a facturar, descripción, período desde/hasta, fecha de emisión, vencimiento
   y condición de venta. Separar la fecha de cobro. Si un pago puede ser neto de
   retenciones y no está claro que sea el total a facturar, aclararlo.
4. Para FCE agregar CBU del emisor, alias opcional y transferencia SCA/ADC; incluir
   referencia comercial si se conoce. No asumir que una FCE ya cobrada no requiere
   posterior cancelación en el Registro FCE; esa gestión no está implementada.
5. Consultar `billing_parameters` para el catálogo oficial IVA y el punto de venta.
   El catálogo no verifica el encuadramiento real del cliente: confirmarlo con el usuario.
6. Corregir ambigüedades antes de preparar: descripción y período discordantes,
   fechas de ejemplo, importe neto/bruto o destinatario diferente. Nunca inventar
   fechas fiscales a partir de un cobro. El motor verifica ventanas técnicas de ARCA;
   no elegir otra fecha silenciosamente si rechaza la recibida.
7. Convertir importe a texto decimal exacto, sin separadores de miles (`"123456.78"`).
   Usar fechas ISO `AAAA-MM-DD`. Ver [esquema](references/INVOICE.md).
8. Usar `preview_invoice` y/o `prepare_invoice`. Mantener una `request_key` estable
   por operación, sin datos sensibles, y reutilizarla ante reintentos.
9. Para el PDF de un borrador ya persistido usar `preview_prepared_invoice` con su
   `draft_id`: conserva el número propuesto exacto. Confirmar el lugar de emisión
   para FCE y pasarlo como `emission_place` al generar el PDF. No inferirlo del
   domicilio ni modificar el resumen persistido. `preview_invoice` no asigna número.

## Confirmar y emitir

Mostrar el resumen exacto: **ambiente, emisor/CUIT, receptor/CUIT, tipo/código,
punto y número propuesto, descripción, total, período, emisión, vencimiento,
condición IVA, condición de venta y datos FCE aplicables**. Adjuntar el PDF borrador
si se solicitó. Todavía no hay factura autorizada.
En FCE incluir también el lugar de emisión confirmado. Pasarlo como `emission_place`
a `issue_invoice`: es obligatorio antes de solicitar CAE y se conserva con el resultado.

Pedir confirmación explícita para esa emisión concreta después de mostrar el resumen.
Una solicitud de investigar, instalar, preparar o automatizar facturas futuras no
autoriza una emisión actual. Si el usuario ya aprobó ese mismo resumen sin cambios
en la conversación, continuar; no pedir permiso de nuevo.

Solo entonces usar `issue_invoice` con `draft_id` y la frase `confirmation` exacta
que devolvió `prepare_invoice`. No afirmar que el hash acredita consentimiento
humano ni escribir una confirmación en nombre del usuario. Ante cambios, preparar
y presentar un nuevo resumen antes de emitir.

Si queda `authorized`, usar `export_invoice_pdf` y comunicar número, CAE, vencimiento
y ambiente. Homologación no tiene validez fiscal. No enviar la factura al cliente
por correo u otra vía sin autorización específica.
Para FCE pasar el lugar confirmado en `emission_place`; la exportación verifica
la vinculación entre resumen y respuesta persistida. No fabricar una vinculación
para resultados antiguos que no la tengan. La regla de 21 días está verificada
solo hasta el 31/10/2026; revisar normativa para fechas posteriores.
El envío FCE comprueba esa vigencia tanto para la fecha del comprobante como para
el día del envío, incluso si se intenta emitir con fecha anterior. La exportación
de un comprobante histórico usa su fecha original y el lugar conservado.

## Guardar y ofrecer correo

1. Usar `invoice_output_options` para proponer fecha/período del nombre y carpeta.
   Preguntar si ambos están bien o se deben cambiar, salvo que el usuario ya haya
   dado esas instrucciones concretas para esta factura. La fecha de archivo deriva
   del período del servicio, no del cobro o de emisión; cambiarla solo cambia el nombre.
2. Mostrar la ruta completa. Una carpeta sincronizada por Drive para escritorio es
   una ruta local: no confundirla con un ID/URL de Drive ni afirmar sincronización
   completada. Si falta la unidad/carpeta, pedir una alternativa y conservar la copia privada.
3. Tras la aprobación, llamar `save_invoice_pdf` con los mismos parámetros y la
   `confirmation` devuelta. Usar `remember_directory=true` solo si el usuario pide
   guardar esa preferencia. No sobrescribir archivos diferentes, borrar copias anteriores
   ni crear otra operación para renombrar. No guardar credenciales o diario en esa carpeta.
4. Ofrecer enviar el PDF por Gmail; preguntar contacto y dirección de correo.
   No asumir que la razón social del receptor identifica a la persona destinataria.
   Resolver nombres ambiguos con contactos/historial o con el usuario. Si falta un dato,
   `prepare_invoice_email` devuelve la pregunta y no prepara un envío.
5. Verificar la cuenta de Gmail del host y pasarla como `sender_email` a
   `prepare_invoice_email` para proponer asunto, cuerpo y PDF desde la factura
   autorizada. Mostrar cuenta Gmail remitente, destinatario, asunto, cuerpo y adjunto.
   Obtener aprobación explícita para ese mensaje; emitir o guardar NO autoriza enviar.
6. Usar únicamente el conector Gmail del host, si dispone de envío con adjuntos.
   Validar la propuesta aprobada con `invoice_email_handoff` y su confirmation exacta.
   Si falla, preparar y mostrar otra propuesta. La herramienta no envía ni prueba
   consentimiento humano. El JSON privado `gmail_payload_path` contiene la estructura MIME y los bytes exactos
   del PDF; no mostrar base64 en el chat. El MCP ARCA no contiene credenciales Gmail
   ni envía mensajes. Revalidar que el adjunto no cambió y que coincide con la propuesta
   aprobada antes de usar el conector. No crear un borrador remoto sin que se haya pedido.
7. Si Gmail no está conectado o no puede adjuntar el PDF, explicar qué falta y conservar
   la propuesta/.eml para envío manual. No sustituir el envío por un enlace público de Drive.
   Informar enviado solo con confirmación e ID del proveedor. Ante respuesta incierta,
   consultar enviados antes de cualquier reintento. La oferta de correo no obliga a enviarlo.

Ver [guardado y Gmail](references/ENTREGA.md). Las preferencias quedan privadas por
perfil y no se incluyen en el repositorio. Las fechas visibles del PDF usan DD-MM-AAAA;
el diario, SOAP y el QR conservan sus formatos técnicos.

## Errores y recuperación

- Para comprobar un comprobante ya autorizado contra ARCA, usar `consult_invoice`
  con ambiente, CUIT emisor, punto, tipo y número explícitos. Siempre consulta
  `FECompConsultar`; comparar receptor, importe, fecha, CAE y vencimiento con el
  resultado local. No usar `invoice_status` como prueba remota ni asumir que
  `reconcile_invoice` consulta ARCA cuando el registro ya está autorizado.
  Si hay diferencias o no se encuentra, informar sin emitir ni modificar el diario.

- `uncertain` o `sending`: llamar `reconcile_invoice`. Nunca reenviar, incrementar
  el número, crear otro borrador equivalente, borrar el diario ni cambiar de carpeta.
- Comprobante no encontrado después de un envío incierto: mantener bloqueo y
  explicar que hace falta revisión. “No encontrado” no prueba que no se emitió.
- Datos remotos diferentes: detener la emisión y mostrar qué campo difiere, sin
  sobrescribir el diario. No atribuirle ese CAE al borrador.
- `rejected`: explicar los códigos y consultar documentación oficial. Corregir los
  datos; usar una nueva clave para un nuevo borrador y obtener aprobación de los cambios.
- `stale`: cambió la numeración; preparar otro borrador y mostrar el número actualizado.
- Nunca rebajar de FCE a C, desactivar TLS, exponer secretos ni modificar controles
  para superar un rechazo. Conservar PDF, detalle y diario; WSFEv1 no guarda ítems.

Para producción, requerir primero una prueba autenticada en homologación y revisión
del PDF. No afirmar que los tests simulados prueban aceptación real de ARCA.
