# Arquitectura y límites operativos

## Capas

Codex usa una skill y herramientas MCP stdio. El motor valida el modelo, guarda un
borrador inmutable y solicita autenticación a WSAA. La clave RSA firma un CMS local;
solo el CMS llega a WSAA. Token y Sign se usan exclusivamente contra WSFEv1. No se
envía la clave privada, la contraseña del llavero ni la clave fiscal a ARCA o al modelo.

Los datos de factura usados en herramientas **sí forman parte de la conversación**
según las condiciones del cliente Codex. “Credenciales privadas” no significa que
importe, CUIT receptor o descripción sean invisibles para el asistente.

## Emisión

1. `preview`: validación y PDF sin CAE, sin red.
2. `prepare`: valida punto de venta/IVA, consulta el último número, persiste resumen
   y hash. No reserva el número en ARCA ni solicita CAE.
3. El cliente presenta el resumen completo y obtiene la aprobación del usuario.
4. `issue`: bloqueo local, verificación de hash/perfil/fecha y de numeración vigente,
   registro durable `sending`, una única solicitud de autorización.
5. Respuesta concluyente: `authorized` o `rejected`. Respuesta perdida, error o
   excepción después de iniciar el envío: `uncertain`.
6. `reconcile`: consulta el mismo punto/tipo/número, compara todos los campos fiscales
   enviados y registra el CAE solo si coinciden. Nunca emite.

Un proceso interrumpido en `sending` también necesita conciliación. Un comprobante
todavía no encontrado mantiene el bloqueo. La liberación manual de una incertidumbre
verdadera requiere investigar con ARCA; no hay botón de reintento automático.

La deduplicación reconoce mismo receptor, tipo, período e importe aunque se cambie
la descripción o fecha. Es deliberadamente conservadora: dos operaciones legítimas
idénticas requieren revisión y no están cubiertas por un override en esta versión.
Un borrador rechazado o con numeración obsoleta puede prepararse de nuevo con otra
`request_key`, conservando su registro anterior. Repetir una factura autorizada
devuelve el resultado persistido, sin solicitar otro CAE.

## Límites de concurrencia

El bloqueo protege todos los procesos que comparten la misma carpeta/diario.
No hay garantía distribuida entre dos computadoras, carpetas o aplicaciones que
usan el mismo punto de venta. **Dedicá el punto de venta a esta instalación.**
Si otro programa consume un número entre la consulta y el envío, no se presume éxito:
se concilia y las diferencias mantienen el bloqueo. Nunca borrar el diario para
resolver un rechazo. No hay un sistema de correcciones: notas de crédito quedan fuera
del alcance inicial.

## Datos y PDF

Para C y FCE C: `ImpNeto = ImpTotal`, IVA/exento/no gravado/tributos = 0; no se envía
array IVA. Una línea de servicios, cantidad 1. La condición IVA del receptor se
consulta en el catálogo oficial, pero su encuadramiento no se verifica en padrón.

FCE: opcional 2101 CBU, 2102 alias (si se informa), 23 referencia comercial y 27
transferencia SCA/ADC. No se informa código 22 de anulación en una factura.
Fechas: servicios ordinarios ±10 días; FCE desde −5 hasta +1, conforme manual v4.7.
Estos son límites técnicos de WSFE, no una recomendación para elegir la fecha fiscal.

WSFEv1 no recibe los ítems. El PDF se arma con el detalle local más la autorización,
no se descarga desde Comprobantes en Línea ni replica sus marcas gráficas. Usa CAE,
vencimiento, identificación, importes y QR oficial en producción. Revisar con el
contador la representación y las obligaciones adicionales antes del primer uso real.
El programa no actualiza categorías de monotributo, IIBB, topes ni Registro FCE.

## Verificación realizada / pendiente

Las pruebas locales verifican las reglas implementadas, el protocolo MCP, firmas
CMS con certificados de prueba y las principales transiciones de emisión. No
demuestran aceptación real de ARCA. Se contrastaron campos con el WSDL oficial
de homologación y el manual v4.7 (01/09/2026). Para habilitar una instalación real
faltan certificados propios, homologación autenticada y revisión fiscal del PDF.
