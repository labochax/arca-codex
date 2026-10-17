# Entrada de factura

`invoice` acepta solamente los campos siguientes. Datos de ejemplo sintéticos:
no usarlos para facturar ni asumir fechas vigentes.

```json
{
  "voucher_type": 211,
  "recipient_cuit": "30712345671",
  "recipient_name": "CLIENTE DE PRUEBA SA",
  "recipient_address": "Domicilio ficticio 456",
  "recipient_iva_id": 1,
  "recipient_iva_label": "IVA Responsable Inscripto",
  "total": "123456.78",
  "invoice_date": "2026-10-16",
  "service_from": "2026-09-01",
  "service_to": "2026-09-30",
  "due_date": "2026-10-16",
  "description": "Asesoría integral - septiembre",
  "sale_condition": "Transferencia bancaria",
  "payment_received_on": "2026-10-09",
  "commercial_reference": "DEMO-SEP-26",
  "cbu": "REEMPLAZAR_POR_CBU_CONFIRMADO",
  "transfer": "SCA"
}
```

`cbu_alias` es opcional. Para Factura C común usar `voucher_type=11` y omitir
`cbu`, `cbu_alias` y `transfer`. `commercial_reference` es opcional también para C.
El emisor y el punto de venta provienen del perfil privado local, no del chat.
`request_key` debe ser estable, entre 8 y 80 letras/dígitos/guiones/guiones bajos.

WSFEv1 recibe importes y datos fiscales. La descripción y condición de venta se
conservan para el PDF local; no se envían como ítems al servicio.

Ventanas del manual v4.7: servicios con C, ±10 días respecto de la solicitud;
FCE C, desde 5 días antes hasta 1 día después. El vencimiento no puede preceder la
fecha de factura. Estas validaciones no determinan cuál es la fecha fiscal correcta.
