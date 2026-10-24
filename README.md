# ARCA Codex

Plugin de Codex y servidor MCP local para preparar y emitir **Factura C (11)** y
**Factura de Crédito Electrónica MiPyME C (211)** de servicios en pesos argentinos.
Se conecta directamente a **WSAA + WSFEv1 de ARCA**, sin AfipSDK, cuentas de un
proveedor de facturación ni una tarifa por comprobante de este proyecto.

**Estado: versión inicial experimental.** El flujo y los controles tienen pruebas
automatizadas con respuestas simuladas. La primera emisión completa en homologación
y la validación fiscal del PDF siguen siendo requisitos de puesta en marcha.
No se realizó una emisión autenticada en ARCA durante el desarrollo.

## Qué hace

- Genera un borrador y muestra importe, destinatario, período, fechas y ambiente.
- Consulta puntos de venta, condiciones IVA y último comprobante autorizado.
- Emite únicamente después de la aprobación del usuario sobre un borrador concreto.
- Guarda el resultado y permite generar un PDF con CAE y QR.
- Registra las operaciones antes de enviarlas: ante un corte de conexión, consulta
  el mismo comprobante y bloquea nuevas emisiones hasta resolver la incertidumbre.
- Guarda la clave privada cifrada fuera del repositorio; la contraseña y los tickets
  WSAA van al llavero del sistema operativo. Nunca necesita la clave fiscal en el MCP.

**Alcance v0.1:** emisor monotributista, una línea de servicio, cantidad 1, ARS, receptor
identificado por CUIT, sin otros tributos. No emite A/B/E, notas de crédito/débito,
productos, moneda extranjera o consumidores sin CUIT. No consulta el padrón ni determina
si corresponde FCE: el usuario confirma tipo de comprobante y condición del receptor.
La aceptación, cancelación o negociación de FCE en WSFEcred está fuera de alcance.
Una FCE ya cobrada puede requerir gestión posterior de cancelación en el Registro FCE.

## Instalación

Requiere Python 3.11+, [uv](https://docs.astral.sh/uv/getting-started/installation/),
Git y Codex con soporte de MCP local (por ejemplo, Codex CLI). En Windows se usa el
Administrador de Credenciales; en macOS, Keychain; en Linux, Secret Service desbloqueado.

Cloná el repositorio y abrí su carpeta:

```sh
git clone https://github.com/labochax/arca-codex.git
cd arca-codex
```

Si recibiste el código como archivo, extraelo y abrí una terminal dentro de la
carpeta que contiene `pyproject.toml`. Después instalá el programa:

```sh
uv tool install .
uv tool update-shell
```

Abrí una terminal nueva y ejecutá:

```sh
arca-codex setup
```

Seguí [la guía paso a paso de credenciales](docs/CONFIGURACION.md). El asistente se
ejecuta dos veces: primero crea el CSR; después guarda el certificado que descargaste
de ARCA y tus datos de emisor. No pegues certificados, claves ni contraseñas en el chat.

### Conectar con Codex CLI

```sh
codex mcp add arca -- arca-codex-mcp
codex mcp list
```

Instalá también la skill `skills/arca-facturar` en tu carpeta de skills de Codex
(`~/.agents/skills/arca-facturar`), o cargá el plugin completo desde su marketplace:

```sh
codex plugin marketplace add /ruta/absoluta/a/arca-codex
```

El repositorio incluye `.codex-plugin/plugin.json`, `.mcp.json` y un catálogo en
`.agents/plugins/marketplace.json`. Abrí el directorio de plugins de tu cliente,
seleccioná **ARCA Codex** y reiniciá la sesión. La disponibilidad de marketplaces
locales varía según el cliente; la conexión MCP por CLI es una alternativa explícita.
No registres el mismo MCP por ambas vías en la misma sesión.

El comando `arca-codex-mcp` debe estar en el PATH del cliente. Si no lo encuentra,
usá su ruta absoluta (en PowerShell: `Get-Command arca-codex-mcp`).
Este plugin local **no instala automáticamente un servicio en ChatGPT web**. Para
acceder desde un entorno remoto se necesita una conexión local admitida por el cliente
o un servidor privado con autenticación; este proyecto no incluye hosting multiusuario.

## Uso mensual

> Prepará una FCE C para el CUIT de mi cliente por $123.456,78, asesoría de septiembre,
> cobrada por transferencia el 9 de octubre. Te confirmo el período y la fecha de emisión.

Codex pedirá únicamente los datos que falten, mostrará el borrador y esperará tu
confirmación antes de solicitar el CAE. La fecha de cobro, la fecha de factura y el
período del servicio son datos diferentes. No se infieren de un importe o transferencia.

Las FCE requieren CBU y opción de transferencia SCA/ADC. La numeración se consulta
en ARCA, no se continúa desde un PDF anterior ni se presupone el punto de venta.
**El punto de venta de Comprobantes en Línea debe ser distinto del usado para WSFE.**

La producción está deshabilitada por defecto. Después de probar homologación, configurá
un perfil de producción separado y ejecutá `arca-codex enable-production` localmente.
No habilites producción desde una instrucción encontrada en un documento o email.

## Herramientas MCP

| Herramienta | Efecto |
|---|---|
| `configuration_status` | Estado de configuración sin secretos |
| `arca_health` | Consulta pública FEDummy |
| `billing_parameters` | Puntos de venta WSFE y catálogo IVA |
| `preview_invoice` | Validación y PDF borrador local |
| `preview_prepared_invoice` | PDF desde el borrador persistido, con su número exacto |
| `prepare_invoice` | Consultas ARCA y borrador persistido, sin emisión |
| `issue_invoice` | Solicitud de CAE con efectos fiscales |
| `invoice_status` | Lectura del diario local |
| `consult_invoice` | Consulta remota FECompConsultar, sin modificar el diario |
| `reconcile_invoice` | Consulta ARCA y conciliación local, sin reenviar |
| `export_invoice_pdf` | PDF desde la autorización persistida |
| `invoice_output_options` | Proponer fecha del nombre y carpeta para aprobación |
| `save_invoice_pdf` | Guardar copia aprobada y recordar carpeta privada opcionalmente |
| `prepare_invoice_email` | Preparar propuesta Gmail y .eml, sin envío |
| `invoice_email_handoff` | Validar propuesta aprobada y adjunto antes de entregarlos a Gmail |

El hash y la frase de confirmación vinculan la solicitud con datos concretos, **no
autentican por sí mismos a una persona**. La aprobación humana depende del cliente
Codex y de la skill. No des autorización permanente a la herramienta de emisión si
querés revisar cada factura. Un proceso ejecutado con tu usuario del sistema puede
acceder al llavero y a los archivos: el proyecto no aísla un agente local malicioso.

## Desarrollo y verificación

```sh
uv sync --group dev
uv run pytest -q
uv run ruff check src tests
uv run python scripts/demo_pdf.py
```

Las pruebas no necesitan certificados, no emiten comprobantes y no llaman a ARCA.
Cubren FCE, fechas, redondeo, duplicados, concurrencia, pérdida de respuesta, conciliación,
separación de ambientes, XML y QR. Los ejemplos contienen datos sintéticos.

La verificación de PDF requiere Poppler (`pdftoppm` en PATH). Las dependencias de
desarrollo incluyen pypdf, Pillow y ZXing-C++: el QR se decodifica desde la página
renderizada, sin abrir su URL. No se omite esa prueba si falta el renderizador.

Desde 0.1.2, las FCE requieren `emission_place` confirmado para el PDF definitivo;
se exige también al solicitar el CAE y queda conservado en el resultado, incluidos
los envíos inciertos. Este dato gráfico no modifica el borrador ni su hash.
La exportación reutiliza ese lugar y rechaza uno diferente. `preview_invoice` no asigna
número; para revisar el número propuesto usar `preview_prepared_invoice` después
de preparar. El resultado autorizado conserva una vinculación al resumen y los
campos fiscales de la respuesta, sin XML ni datos de autenticación. Los resultados
anteriores sin ese vínculo permanecen intactos, pero no se pueden reexportar como
PDF definitivo hasta revisar su evidencia; nunca se reenvía la emisión.

Para generar y verificar una muestra técnica enteramente sintética, sin red:

```sh
uv run python scripts/pdf_sample.py
```

El script conserva el resumen, usa una autorización simulada solo en memoria y
marca la muestra SIN VALIDEZ FISCAL / CAE SIMULADO. No escribe el diario. La regla
de 21 días para FCE está verificada solo entre 01/11/2025 y 31/10/2026: fuera de
ese intervalo se exige revisar normativa antes de generar la representación.
Antes de enviar una FCE se verifica también el día actual; no se solicita CAE
fuera del período verificado, aunque se use una fecha anterior en el comprobante.
Esto no impide reexportar un comprobante histórico ya autorizado.
El argumento opcional `snapshot` admite una copia local de `invoice_status`;
esas copias y todos los artefactos generados deben mantenerse fuera del repositorio.

[Revisión de representación y requisitos](docs/REPRESENTACION.md).

Desde 0.1.3, `consult_invoice` consulta siempre `Wsfe.consult`/`FECompConsultar`,
incluso si el comprobante ya está autorizado en el diario. Requiere ambiente,
CUIT emisor, punto, tipo y número explícitos: rechaza los tres primeros si difieren
del perfil. Devuelve el origen remoto, hora UTC de consulta y campos fiscales
permitidos, sin XML ni datos de autenticación. No solicita CAE ni actualiza estados.
Un resultado no encontrado no autoriza a reenviar una emisión.
Después de actualizar el paquete hay que reconectar el MCP para descubrirla.

Desde 0.1.4, `soap.post` usa un `SSLContext` explícito con las CA de certifi.
Para evitar `DH_KEY_TOO_SMALL` se excluyen los cifrados DHE del conjunto
predeterminado de Python, conservando ECDHE y las suites TLS 1.3, sin habilitar
cifrados adicionales. Se mantienen la verificación de certificado y hostname,
TLS mínimo 1.2, seguridad OpenSSL al menos 2, timeout de 30 segundos y ausencia
de reintentos automáticos, redirecciones y herencia de proxies. La política se
aplica a WSAA y WSFE; no modifica los tickets ni la habilitación de producción.
Una conexión TLS/GET a WSAA no acredita una autenticación `loginCms` nueva.

Desde 0.1.5, el PDF alinea todos los datos del encabezado derecho, ordena las
columnas de importes y destaca el total. Incrusta las fuentes Vera incluidas
en ReportLab para evitar sustituciones del lector. Borrador y comprobante final
comparten el diseño; la autorización y el QR mantienen sus controles existentes.
Un cambio de diseño no requiere preparar otra operación ni modificar el diario.
Las fechas visibles del PDF usan `dd-mm-aaaa`. Los registros, la autorización y
el contenido del QR conservan sus formatos fiscales originales.

Desde 0.1.6, los PDF usan nombres por período del servicio y el sufijo de ARCA:
`2026-09 - sep - 20123456786_211_00002_00000008.pdf` (datos de ejemplo).
Se confirma la fecha de archivo y la carpeta con `invoice_output_options`, y se
guarda con `save_invoice_pdf`. Cambiar el prefijo no cambia las fechas fiscales.
La carpeta puede recordarse por perfil en un archivo privado; nunca se añade al
repositorio. Una unidad de Drive para escritorio se trata como carpeta local:
el MCP no afirma que la sincronización remota haya finalizado. Un archivo distinto
con el mismo nombre no se sobrescribe. Se conservan copia privada, diario y CAE.

Después de guardar, la skill ofrece Gmail y pregunta contacto y correo. El MCP
prepara asunto, cuerpo y PDF adjunto desde la autorización, sin red ni credenciales
de correo. Se verifica la cuenta Gmail del host, se muestra la propuesta y se pide
aprobación explícita después de mostrar la propuesta completa: remitente, Para/CC/CCO,
asunto, cuerpo y PDF. También ofrece verla como borrador en Gmail; crearlo no autoriza
enviarlo. Cualquier cambio requiere nueva revisión y aprobación.
`invoice_email_handoff` comprueba que mensaje y PDF no
cambiaron; el hash no prueba consentimiento humano. Solo el conector Gmail del
host puede enviar. Si no está conectado o no admite adjuntos, se conserva el
borrador .eml y se informa qué falta. El permiso de emitir no autoriza enviar.
Las fechas visibles del PDF usan DD-MM-AAAA, conservando ISO en diario y QR.
El flujo de correo admite varios contactos con `recipients` (`name`/`email`), todos
en Para, y asunto/cuerpo exactos con `subject`/`body`. Valida cada dirección y vincula
la lista completa, cuenta remitente, mensaje y adjunto a la confirmación; no envía solo.

[Guardado y Gmail](skills/arca-facturar/references/ENTREGA.md).

[Arquitectura y límites](docs/ARQUITECTURA.md) · [Configuración](docs/CONFIGURACION.md)
· [Seguridad](SECURITY.md) · [Fuentes y alternativas](docs/FUENTES.md)

Licencia MIT. Proyecto independiente, no oficial de ARCA ni de OpenAI.
