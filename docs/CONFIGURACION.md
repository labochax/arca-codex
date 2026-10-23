# Configurar una vez, facturar desde Codex

## 1. Preparar la computadora

Usá tu propia cuenta de Windows, macOS o Linux. Instalá Python 3.11 o posterior,
uv y el código como indica el README. No uses una computadora pública.

Por defecto, los datos privados se guardan en `~/.arca-codex`, equivalente en
Windows a `C:\Users\TU_USUARIO\.arca-codex`. Esta carpeta está fuera del proyecto
y no se sube a GitHub. El programa rechaza una carpeta de datos dentro de un repo.

En Windows, creá la carpeta y limitá su acceso desde Propiedades → Seguridad a tu
usuario y las cuentas de administración/sistema necesarias. `chmod` no administra
ACL de Windows. El cifrado de la clave y el Administrador de Credenciales se usan
también en Windows. Protegé el disco con BitLocker/FileVault o equivalente.

En Linux necesitás un Secret Service desbloqueado (por ejemplo GNOME Keyring).
No se admite `keyrings.alt` ni almacenamiento en texto plano. En servidores sin
sesión gráfica, configurá un llavero seguro antes de instalar; este walkthrough
está orientado a una computadora personal. El asistente falla si no lo encuentra.

## 2. Usar homologación primero

Homologación es el ambiente de prueba, sin validez fiscal. Producción es real.
Usá carpetas diferentes para que perfiles, certificados y diarios no se mezclen.

PowerShell:

```powershell
$env:ARCA_CODEX_HOME = Join-Path $env:USERPROFILE '.arca-codex-homo'
arca-codex setup
```

macOS/Linux:

```sh
export ARCA_CODEX_HOME="$HOME/.arca-codex-homo"
arca-codex setup
```

Elegí `homologacion`, tu CUIT y tu nombre. El asistente genera la clave cifrada y un
archivo `request.csr`. Muestra solamente las rutas. **El CSR puede enviarse a ARCA;
el archivo `private-key.pem` nunca se sube ni se comparte.**

Entrá al portal oficial de ARCA con tu clave fiscal **vos mismo**. Para pruebas,
adherí/abrí **WSASS – Autogestión de Certificados Homologación**. Creá un certificado
con el CSR y autorizá el servicio `wsfe` para el CUIT representado. No se pide
autorización con identificador `wsfev1`: ese es el nombre de la interfaz; el ticket
WSAA utiliza `wsfe`.

Descargá el certificado. Ejecutá de nuevo `arca-codex setup`, con el mismo ambiente
y CUIT. Ingresá domicilio, IIBB, inicio de actividades, punto de venta de pruebas y
ruta del certificado. La clave debe corresponder al certificado; se verifica antes
de aceptar el perfil. Para representación delegada, verificá la relación con el
CUIT emisor: el CUIT del certificado no tiene que ser necesariamente el representado.

## 3. Verificar sin emitir

```sh
arca-codex doctor
```

Debe mostrar certificado vigente, servicios disponibles, punto de venta y condiciones
IVA. Si falla: revisá ambiente correcto, certificado vigente, autorización `wsfe`,
CUIT representado y fecha/hora del equipo. No envíes capturas con secretos a un issue.
En homologación, la consulta de puntos puede responder `602` (sin registros).
Desde v0.1.1, esto se muestra como catálogo vacío: `doctor` consulta además el último
número de FCE C para el punto configurado. Una respuesta con número `0` significa
que esa consulta no registra comprobantes anteriores, no que la emisión esté
habilitada. Preparar también requiere consultar el último número del tipo solicitado;
la aceptación se verifica al autorizar el comprobante de prueba. Si el catálogo sí
devuelve puntos, se siguen rechazando los ausentes, bloqueados o dados de baja.
En producción, `602` no activa esta alternativa y el punto debe figurar habilitado.
No se elige automáticamente el punto `1`: debe seleccionarse y consultarse de forma
explícita en homologación, conservando el mismo perfil. No crear un punto de venta
real para resolver por suposición una consulta del ambiente de pruebas.
Si WSAA informa que ya existe un ticket, no borres el llavero ni pidas tickets en un
bucle: el cache conserva el ticket hasta su vencimiento. Otros programas que usan
el mismo certificado pueden interferir; usá un certificado dedicado.

## 4. Conectar el perfil al cliente

El MCP debe recibir **la misma** variable `ARCA_CODEX_HOME` que usaste en setup.
Es una ruta local, no una contraseña. Ejemplo de `~/.codex/config.toml`:

```toml
[mcp_servers.arca]
command = "arca-codex-mcp"
args = []

[mcp_servers.arca.env]
ARCA_CODEX_HOME = "/ruta/absoluta/a/.arca-codex-homo"
```

En Windows usá una cadena literal TOML: `ARCA_CODEX_HOME = 'C:\Users\TU_USUARIO\.arca-codex-homo'`.
No guardes esa configuración personal en el repo. En la configuración del plugin
podés añadir la misma variable a `env` del servidor. Reiniciá Codex después de cambiarla.

Prepará y autorizá un comprobante de prueba. Revisá su consulta, PDF y datos fiscales,
incluidos los opcionales 2101/2102/23/27 cuando corresponda. El PDF de homologación
lleva una marca visible y no lleva un QR que parezca validar una factura real.

## 5. Pasar a producción

1. Cambiá `ARCA_CODEX_HOME` a otra carpeta, por ejemplo `.arca-codex-prod`.
2. Ejecutá `arca-codex setup` y elegí `produccion`. Se genera un nuevo CSR y otra clave.
3. Entrá a ARCA → **Administración de Certificados Digitales**. Creá un alias,
   subí el CSR y descargá el certificado. Los nombres de pantalla pueden cambiar.
4. En **Administrador de Relaciones de Clave Fiscal**, creá una relación para
   **Facturación Electrónica / wsfe**, seleccionando el certificado/computador y
   el CUIT representado. Confirmá la relación si el portal lo requiere.
5. En **Administración de Puntos de Venta y Domicilios**, creá un punto de venta
   asociado a la modalidad de factura electrónica por **Web Services para
   monotributistas** que muestre ARCA. No reutilices el de Comprobantes en Línea.
6. Ejecutá setup otra vez e ingresá ese punto de venta y el certificado descargado.
7. Ejecutá `arca-codex doctor` y verificá que el punto aparezca habilitado.
8. Ejecutá `arca-codex enable-production` y escribí la frase que solicita.
9. Actualizá la ruta del perfil del MCP y reiniciá Codex. Comprobá que muestre
   `produccion` y el CUIT correcto antes de preparar la primera factura.

Para FCE, verificá además el CBU registrado/habilitado y la situación del receptor.
El plugin comprueba formato y dígitos, pero ARCA valida si esa cuenta corresponde.
El régimen aplicable y las fechas deben reflejar la operación real. La fecha de cobro
no se usa automáticamente como fecha de factura ni como vencimiento.

## 6. Respaldo y renovación

Respaldá la carpeta privada **junto con el mecanismo de recuperación del llavero**.
Copiar el PEM cifrado solo no permite recuperarlo: la contraseña se conserva en el
llavero del sistema. Conservá también el diario y los PDF: WSFE no almacena el
detalle de los servicios para reconstruirlo. No borres un diario con estados inciertos.

Esta versión no tiene asistente de rotación ni exportación de claves. Antes del
vencimiento del certificado, suspendé emisiones y prepará la renovación conservando
el diario y reconciliando pendientes. Si se pierde acceso al llavero, generá nuevas
credenciales mediante ARCA; no pruebes contraseñas por chat.

Cada usuario de GitHub instala el mismo código, pero configura **su propia carpeta,
CUIT, relación, certificado y llavero**. Forkear el repo no copia tus credenciales.

Guías oficiales: [certificados](https://www.afip.gob.ar/ws/documentacion/certificados.asp),
[WSAA y relaciones](https://www.afip.gov.ar/ws/documentacion/wsaa.asp),
[manual de WSASS](https://www.afip.gov.ar/ws/wsass/html/index.html).
