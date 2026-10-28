# Categorías, perfiles y empresas

## Contenido

- [Qué soporta](#qué-soporta)
- [Control del monotributo](#control-del-monotributo)
- [Alta de otro emisor](#alta-de-otro-emisor)
- [Empresas](#empresas)
- [Puesta en marcha y límites](#puesta-en-marcha-y-límites)

## Qué soporta

| Emisor | Comprobantes de servicios en ARS | Condiciones |
|---|---|---|
| Monotributo, categorías A–K | C 11; FCE C 211 | Sin IVA discriminado |
| Exento en IVA | C común 11 | Confirmar inscripción |
| Responsable inscripto | A común 1; B 6 | Neto e IVA explícitos; una alícuota; sin otros tributos |
| Un tercero de cualquiera de los anteriores | Los del régimen representado | Relación WSFE válida y perfil propio |

Las variantes M, A con retenciones/leyendas especiales, FCE A/B, E, notas de crédito/débito,
productos, moneda extranjera, varias alícuotas o conceptos exentos/no gravados mezclados
no están soportadas. No convertir una operación fuera de alcance para hacerla pasar.

## Control del monotributo

La categoría no cambia el SOAP C/211. A–K tienen topes de ingresos anuales distintos.
`monitor.py` conserva la escala oficial con aplicación desde el 01/08/2026, verificada
el 28/10/2026. `REVIEW_BY=2027-01-31` es una fecha conservadora de revisión del software,
NO una afirmación de fecha legal de vencimiento. Si se publica otra escala hay que
revisarla, actualizar los valores, fecha y pruebas; no se hace scraping silencioso.
Un límite personal es preventivo y NO modifica los topes oficiales ni la categoría ARCA.

1. Pedir la categoría actualmente inscripta; no inferirla del ingreso mensual.
2. Importar todas las facturas relevantes del mismo CUIT y TODOS sus puntos/canales,
   incluyendo Comprobantes en Línea y otros perfiles. El MCP no descarga un libro
   completo de ARCA: `FECompConsultar` consulta comprobantes individuales conocidos.
3. Mostrar los datos a importar y llamar `import_revenue_history`. Cada entrada contiene
   `issuer_cuit`, `voucher_type`, `point_of_sale`, `number`, `invoice_date`, `total`
   (decimal exacto ARS), `currency="ARS"`, `kind` (`invoice`, `debit`, `credit`) y `source`.
   No necesita nombres ni datos bancarios para el control. No importar movimientos de
   banco como facturas, ni una factura local no autorizada como si fuera una real.
4. Confirmar con el usuario el intervalo cubierto por TODO su historial. Usar
   `configure_revenue_control` con `category`, `warning_percent` (80 por defecto),
   `personal_limit` opcional, `coverage_from`, `coverage_through` y `coverage_confirmed`.
   La cobertura es una declaración del usuario; el programa no la certifica con ARCA.
5. Consultar `revenue_status`, que muestra el acumulado de los últimos doce meses
   por fecha de comprobante, no por año calendario. Una factura atrasada, anticipos,
   ingresos devengados sin comprobante o imputaciones particulares requieren revisión
   contable: este es un control preventivo, NO una determinación fiscal ni una constancia
   de correcta categorización. No calcula otros parámetros ni causales de exclusión. Una actividad iniciada hace menos
   de doce meses genera advertencia: no implementa anualización ni recategorización de inicio.
6. Pasar la próxima factura como `candidate` para ver el acumulado resultante antes de
   emitir. `monthly_projection` calcula un escenario de doce importes iguales y si
   superaría topes; no predice fechas de cobro ni el acumulado móvil futuro.
7. Si se aproxima o supera la categoría, revisar las advertencias con el usuario. Al
   emitir, `revenue_confirmation` vincula su revisión al cálculo vigente. Si cambia el
   historial, la categoría o el día, recalcular y mostrarlo de nuevo. Un hash no prueba
   aprobación humana. No recategorizar, diferir, omitir o dividir artificialmente facturas.

Solo se suman autorizadas de producción del journal de esa carpeta y el historial
importado. Las `sending`/`uncertain` se suman como exposición conservadora. No se cuentan
homologación, borradores o rechazadas. La identidad CUIT/tipo/punto/número evita duplicar
comprobantes importados y locales; una diferencia bloquea el flujo hasta revisar.
El importador es transaccional: un conflicto no deja la mitad del lote guardado.
Las notas de crédito se registran e informan SIN deducción automática, para no crear
margen aparente sin revisar su imputación; el resultado puede sobreestimar ingresos.

La falta de cobertura bloquea la emisión con control configurado; no es margen real. La superación de la categoría
se distingue del máximo K: este último, una escala no revisada o un conflicto bloquean
la emisión bajo el control. No existe desbloqueo mediante cambio ficticio de categoría.
El control se repite dentro del bloqueo de emisión inmediatamente antes del envío.

Para instalaciones anteriores sin política, la configuración no se altera: el monitor
informa `configured=false` y no asegura margen; las emisiones mantienen el flujo previo.
Para activarlo hay que configurar categoría e historial, NO reinstalar certificados.
Las alertas aparecen al usar las herramientas; no se programa ningún envío o vigilancia.

## Alta de otro emisor

Cada conexión MCP fija su perfil. No hay un cambio implícito de contribuyente dentro de
una conversación o una herramienta de emisión.

```powershell
arca-codex --profile cliente-homo setup
arca-codex --profile cliente-homo doctor
arca-codex profiles
```

Esto conserva el perfil predeterminado y crea una carpeta privada `profiles/cliente-homo`
dentro de la carpeta base `ARCA_CODEX_HOME`. El asistente distingue:

- CUIT emisor/representado: figura en la factura y en `Auth.Cuit` de WSFE.
- CUIT titular del certificado: firma WSAA; puede ser un representante autorizado.
- Régimen del emisor: monotributo, responsable inscripto o exento.

El tercero completa y acepta la relación WSFE en el Administrador de Relaciones de ARCA.
No comparte su clave fiscal por chat. La confirmación local no crea ni sustituye esa
relación. `doctor` consulta WSFE para ese emisor; un FEDummy exitoso no prueba delegación.
Cada perfil conserva certificados, passwords en llavero, journal, política, salidas y
registro de correo separados. Para nuevos certificados las cuentas del llavero incluyen
el perfil; los CSRs anteriores conservan su cuenta original. No se rotan claves al actualizar. Los tickets WSAA se reutilizan por certificado,
ambiente y servicio, con bloqueo compartido entre perfiles; `Auth.Cuit` conserva el
representado de cada llamada. Un ticket anterior válido se migra sin borrarlo ni
forzar otro login. No borrar tickets ni reintentar si ARCA informa `coe.alreadyAuthenticated`.

Si el representante ya tiene certificado y clave, usar `arca-codex --profile cliente-homo setup --existing`.
Ingresar sus rutas y contraseña solo en la terminal local. El programa verifica CUIT, par,
vigencia y RSA; guarda una copia cifrada y su contraseña nueva en el llavero. Los archivos
originales no se modifican. La relación del emisor a ese certificado sigue siendo necesaria.

Registrar un MCP distinto, con nombre reconocible, apuntando al mismo ejecutable:
`ARCA_CODEX_HOME` = carpeta base privada, `ARCA_CODEX_PROFILE` = `cliente-homo`.
El usuario debe seleccionar explícitamente esa conexión y verificar su emisor antes de
preparar. Otra conexión existente sigue apuntando a su perfil original. El listado de
perfiles no cambia ninguna conexión. Para producción usar otro perfil y habilitación
local explícita después de homologar los tipos de comprobante que se van a usar.

Si un mismo CUIT tiene varios puntos/perfiles, sus journals no se consolidan entre
carpetas automáticamente: importar los demás en el control antes de confirmar cobertura.
La carpeta sincronizada de Drive sirve para PDFs, no para la carpeta privada del perfil.

## Empresas

Para RI: confirmar que ARCA habilita A común antes de marcar `ordinary_a_authorized`.
No basta con tener IVA inscripto: podría corresponder A con leyenda o M. Confirmar además
si la operación exige FCE; FCE A/B está fuera de alcance de esta versión.

Ejemplo sintético de importes A/B: `net_amount="1000.00"`, `vat_id=5`,
`vat_amount="210.00"`, `total="1210.00"`. Tasas soportadas: 0, 10,5, 21 y 27%,
que se contrastan contra `FEParamGetTiposIva` al preparar. No se elige una tasa por defecto.
El motor valida neto positivo, IVA redondeado HALF_UP a centavos y suma exacta.
Para A admite receptor RI o monotributista; B admite exento o consumidor final con CUIT.
La condición y descripción se contrastan con el catálogo por clase A/B/C.

PDF: letra/código y régimen correctos, neto/IVA/total explícitos, leyenda Ley 27.618 para
A a monotributista, y transparencia fiscal para B. El QR utiliza el TOTAL y el código
exacto. No hay cálculos automáticos de percepciones, retenciones ni liquidación de IVA.

## Puesta en marcha y límites

- Actualizar el programa y las cuatro skills; conservar el perfil existente.
- No habilitar nuevos emisores ni producción automáticamente.
- Probar cada clase de factura y representación con sus credenciales en homologación,
  consultar la autorización, revisar PDF y QR antes de producción.
- Los tests del proyecto son sintéticos; no acreditan aceptación real de A/B ni de una
  delegación concreta por ARCA.
- Conservar las aprobaciones separadas de emisión, guardado y correo; confirmar cuenta
  remitente, destinatarios y adjunto. No enviar avisos a terceros por configurar alertas.
- La vigencia FCE C sigue su revisión independiente, actualmente hasta 31/10/2026.
  Esta ampliación NO prolonga una norma anterior sin verificar otra fuente.

Fuentes primarias verificadas el 28/10/2026:

- https://www.arca.gob.ar/monotributo/categorias.asp
- https://www.arca.gob.ar/monotributo/ayuda/recategorizacion.asp
- https://www.arca.gob.ar/monotributo/ayuda/exclusion.asp
- https://biblioteca.afip.gob.ar/dcp/LEY_C_024977_1998_06_03
- https://serviciosweb.afip.gob.ar/facturacion/regimen-general/comprobantes.asp
- https://www.afip.gob.ar/ws/documentacion/manuales/manual-desarrollador-ARCA-COMPG.pdf
- https://www.afip.gob.ar/ws/WSAA/wsaa_obtener_certificado_produccion.pdf
- RG 5614/2024, transparencia fiscal, Biblioteca Electrónica de ARCA.

- https://www.afip.gob.ar/ws/WSAA/Especificacion_Tecnica_WSAA_1.2.2.pdf
