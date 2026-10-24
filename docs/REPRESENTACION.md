# Representación gráfica - revisión 24/10/2026

Alcance: Factura C (11) y FCE MiPyMEs C (211), una línea de servicios, ARS,
receptor identificado por CUIT. Esta revisión no solicita CAE ni habilita producción.

## Fuentes oficiales contrastadas

- [RG 1415, texto actualizado, Anexo II A y B](https://www.argentina.gob.ar/normativa/nacional/resoluci%C3%B3n-1415-2003-81316/actualizacion):
  identificación de las partes y distribución de los datos.
- [Ley 27.440, art. 5](https://www.argentina.gob.ar/normativa/nacional/ley-27440-310084/texto):
  lugar, fecha, vencimiento, identificación, CBU/alias, importe en letras y números,
  y leyendas de aceptación, carácter ejecutivo y transferencia de información.
- [Resolución 219/2025, art. 1](https://biblioteca.arca.gob.ar/search/query/norma.aspx?p=t:RES%7Cn:219%7Co:144%7Ca:2025%7Cf:24/10/2025):
  plazo excepcional de 21 días corridos entre 01/11/2025 y 31/10/2026. El cómputo
  depende de la recepción en el domicilio fiscal electrónico, no de la emisión o
  del vencimiento de pago. No se calcula una fecha de aceptación sin esa recepción.
- [RG 4367/2018, texto vigente](https://biblioteca.afip.gob.ar/dcp/REAG01004367_2018_12_19),
  modificada por RG 4919/2021 y 5764/2025: código 211, entrega, remitos si existen,
  opción de transmisión y recepción/aceptación (arts. 3, 4, 7, 9, 10, 14 y 15).
- [Resolución 103/2020, art. 3 b](https://www.argentina.gob.ar/normativa/nacional/resoluci%C3%B3n-103-2020-343167/texto):
  circulación SCA/ADC; es una leyenda distinta de la de aceptación del art. 5.
- [Especificación oficial del QR](https://www.afip.gob.ar/fe/qr/documentos/QRespecificaciones.pdf):
  URL `https://www.arca.gob.ar/fe/qr/`, JSON Base64 versión 1. Importe y código de
  autorización son valores JSON numéricos. Moneda PES, cotización 1, tipo de
  documento 80 y tipo de autorización E para el alcance implementado.
- [WSFEv1 v4.7, revisión 01/09/2026](https://www.afip.gob.ar/ws/documentacion/manuales/manual-desarrollador-ARCA-COMPG.pdf):
  campos de FECAESolicitar/FECompConsultar que vinculan respuesta y comprobante.

## Cambios y comprobación

- Letra C central destacada, código debajo; emisor/domicilio/condición IVA a la
  izquierda; número centrado en el recuadro derecho, fecha, CUIT, IIBB e inicio de
  actividades a la derecha. Ambos recuadros laterales miden al menos 72 x 40 mm.
- Identificación del receptor debajo del encabezado, período, vencimiento y venta.
- Importe en números y letras mediante Decimal, sin conversión monetaria a float;
  admite el rango del modelo (13 enteros y 2 decimales), apócope, millones y billones.
- Lugar de emisión confirmado por el usuario, separado del domicilio comercial.
  Sin él, un borrador muestra la falta; se bloquean tanto el envío FCE como su PDF
  definitivo. Se muestra al usuario antes de aprobar y se pasa a `issue_invoice`
  como `emission_place`. Se conserva con el resultado y los estados inciertos,
  sin alterar el resumen fiscal ya persistido. La exportación reutiliza ese lugar
  y rechaza uno distinto; las exportaciones históricas conservan su fecha original.
- Leyendas de aceptación/ejecutividad y consentimiento de transferencia de
  información separadas de la leyenda de circulación SCA/ADC. La regla temporal
  se bloquea fuera de su vigencia verificada, sin asumir prórrogas futuras. Antes
  de solicitar CAE se verifican el día del envío y la fecha de factura, también
  cuando se intenta enviar un comprobante fechado antes de vencer la regla.
- PDF definitivo solo desde estado autorizado persistido; CAE, vencimiento y QR
  de producción. El hash del borrador y los campos fiscales de la respuesta se
  comprueban al exportar. Se rechazan número, emisor, punto, tipo, receptor, fecha,
  total o contenido local alterados, así como CAE/vencimiento distintos de la respuesta.
- FECAESolicitar no devuelve todos los importes ni los ítems: se vincula su
  respuesta a la solicitud y al resumen exacto, verificando los campos que devuelve.
  En conciliación se comparan todos los campos fiscales consultados y los opcionales.
  Esto es consistencia local; no constituye una firma digital ni prueba adicional
  de aceptación del régimen por ARCA.
- Se conservan los resultados anteriores sin modificarlos; si no tienen evidencia
  de vinculación, se exige revisión antes de una nueva exportación definitiva.

## Ensayo reproducible con datos sintéticos

`uv run python scripts/pdf_sample.py` no requiere configuración ni una factura
existente. Usa un emisor y receptor ficticios, identificadores de fixtures, CBU
sintético, importes de demostración y una localidad explícitamente ficticia.
Genera un borrador sin CAE y una muestra técnica marcada SIN VALIDEZ FISCAL / CAE
SIMULADO, sin guardar una autorización ni escribir un diario.

El script renderiza ambos PDF con Poppler a 180 DPI. ZXing decodifica el QR de la
imagen y verifica la URL, los campos, sus valores y los tipos JSON contra el
resumen de la muestra. No abre la URL ni consulta ARCA. La copia del resumen
permanece intacta. Los tests también verifican el encabezado, el importe en letras,
la coherencia de la autorización, el número persistido y la separación de ambientes.

El argumento opcional `snapshot` permite trabajar con una copia local exportada
mediante `invoice_status`. El lugar de emisión de esa copia debe ser confirmado
por el usuario y pasarse con `--emission-place`; no se infiere del domicilio.
Esa copia, el informe JSON y los PDF/PNG generados son privados y nunca deben
incluirse en commits ni en un PR. Todas las salidas van por defecto a `output/pdf/`,
ignorada por Git. Los ejemplos incorporados al repositorio son sintéticos.

## Verificación y dependencias

- `uv sync --frozen --group dev` valida la instalación desde el lockfile.
- `uv run pytest -q` incluye las pruebas del PDF y de comunicación MCP.
- `uv run ruff check src tests scripts` revisa código, pruebas y scripts.
- `uv run python scripts/pdf_sample.py` permite repetir el ensayo del QR sin datos
  fiscales ni acceso a servicios externos. La muestra se revisa además visualmente.
- Se requiere Poppler (`pdftoppm` en PATH); el workflow lo instala en Linux y
  Windows. La verificación no omite silenciosamente la prueba si falta Poppler.

## Límites y puesta en marcha

La autorización y los datos del ensayo son simulados. La muestra QR no prueba
aceptación por ARCA, autenticación ni encuadramiento real en el régimen FCE.
Antes de producción siguen siendo necesarias una prueba autenticada autorizada
en homologación y la revisión de los datos de la operación real.

La regla temporal se verifica solo para el intervalo 01/11/2025 a 31/10/2026.
Para fechas posteriores al 31/10/2026 se bloquea la representación y hay que
revisar fuentes oficiales, actualizar la regla y sus pruebas. No se presupone
una prórroga ni el retorno automático a otro plazo. Tampoco se calcula una fecha
de aceptación sin conocer la recepción en el domicilio fiscal electrónico.

Tras instalar 0.1.2 se debe reconectar el MCP para cargar el código nuevo y
`preview_prepared_invoice`. La actualización del paquete no debe modificar
perfiles, certificados, tickets ni diarios. Los resultados antiguos sin evidencia
de vinculación permanecen intactos; se exige revisión antes de reexportarlos.
No se implementa el Registro FCE ni la cancelación posterior de facturas cobradas.
