---
name: arca-empresas
description: Preparar facturas de servicios domésticos en pesos para empresas responsables inscriptas o exentas con ARCA Codex. Usar para facturas A/B con IVA y C de emisores exentos, elección de régimen y pruebas de homologación antes de producción.
---

# Facturación de empresas

1. Consultar `configuration_status` del MCP y verificar CUIT del emisor, régimen y ambiente. Usar `arca-perfiles` para representación o alta. Una empresa no se transforma en monotributista por elegir una categoría. No usar el perfil personal para facturar ingresos de una sociedad.
2. Confirmar con el usuario su inscripción y autorización: responsable inscripto admite A común (1) o B (6); exento admite C común (11) en este alcance. A común requiere `ordinary_a_authorized` confirmado localmente: no sustituir comprobantes M, A con leyendas ni retenciones especiales. Si se exige FCE A/B, detener este flujo y explicar que aún no está implementado; no emitir una común para eludir la obligación.
3. Limitar a una línea de servicios domésticos, ARS, receptor identificado por CUIT, una alícuota y sin otros tributos. Rechazar bienes, exportaciones, moneda extranjera, varias alícuotas, conceptos exentos/no gravados mezclados, notas de crédito/débito, percepciones o retenciones que este modelo no calcula. No prometer una liquidación completa de IVA ni presentación de declaraciones.
4. Reunir descripción, período, emisión, vencimiento, condición de venta, razón social, domicilio y condición IVA del receptor. Separar fecha de cobro. Confirmar neto, IVA y total; no aplicar 21% por defecto ni tratar el pago neto de retenciones como total de la operación.
5. Consultar `billing_parameters(voucher_class="A" o "B" o "C")` para condiciones oficiales y alícuotas vigentes. A es para receptores RI o monotributistas; B para exentos o consumidores finales identificados por CUIT en este alcance. Para emisor exento usar C sin discriminación de IVA. No elegir clase solo por el nombre «empresa» del cliente.
6. Para A/B informar `net_amount`, `vat_id` (3=0%, 4=10,5%, 5=21%, 6=27%), `vat_amount` y `total` como decimales exactos. El motor comprueba IVA redondeado a centavos y neto + IVA = total. No ajustar totales silenciosamente. C rechaza esos campos.
7. Preparar con `prepare_invoice`, mostrar resumen íntegro y PDF. Revisar letra/código, régimen del emisor, importes y leyendas. A a monotributista incluye leyenda de Ley 27.618. B incluye IVA contenido y apartado de transparencia; no ocultarlo. Confirmar la pertinencia tributaria antes de aprobar.
8. Emitir SOLO después de aprobación explícita del resumen exacto y siguiendo idempotencia/conciliación de `arca-facturar`. `uncertain` exige conciliar, nunca otro envío. Comprobar autorización y QR, guardar con herramientas de archivo y aplicar revisión del correo antes de cualquier envío. No usar un CAE simulado como evidencia real.
9. Probar A/B y representación en homologación con credenciales propias antes de habilitar cada perfil productivo. Los tests del proyecto son simulados y no demuestran aceptación de ARCA de una empresa real. No generar facturas reales para probar el programa.

Fuentes: [clases de comprobante](https://serviciosweb.afip.gob.ar/facturacion/regimen-general/comprobantes.asp), [WSFEv1](https://www.afip.gob.ar/ws/documentacion/manuales/manual-desarrollador-ARCA-COMPG.pdf), [transparencia fiscal](https://biblioteca.arca.gob.ar/search/query/norma.aspx?p=t%3ARAG%7Cn%3A5614%7Co%3A9%7Ca%3A2024%7Cf%3A12%2F12%2F2024). Revisar normas y encuadramiento; no reemplazar al contador.
