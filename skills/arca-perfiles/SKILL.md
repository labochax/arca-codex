---
name: arca-perfiles
description: Configurar emisores propios, terceros autorizados y empresas con perfiles separados de ARCA Codex. Usar para múltiples CUIT, delegación WSFE, selección del emisor, certificados y ambientes, sin compartir claves fiscales ni mezclar diarios.
---

# Perfiles y representación

1. Consultar `configuration_status` y `list_billing_profiles` si están disponibles. Mostrar siempre CUIT EMISOR (representado), titular del certificado, régimen, ambiente y punto de venta. Si faltan herramientas, guiar la actualización del MCP local; no inventar su estado.
2. Usar un perfil separado por emisor, ambiente y punto de venta. No modificar el perfil de una conexión activa para superar errores ni mover certificados de un contribuyente a otro. Los diarios, producción habilitada, preferencias de archivo, propuestas de correo y control de ingresos quedan separados.
3. Guiar en terminal local `arca-codex --profile cliente-homo setup`. El asistente pregunta CUIT del emisor y del titular del certificado. Repetir con otro nombre para producción. El usuario ingresa la clave fiscal únicamente en ARCA. No pedir por chat claves privadas, contraseñas, certificado completo, tickets ni tokens.
4. Para operar por un tercero, completar y aceptar en ARCA la relación del servicio WSFE al certificado/representante autorizado. Ser dueño de una empresa o conocer su CUIT no constituye autorización técnica. `delegation_confirmed` es una declaración local, no prueba remota ni permiso concedido por el programa.
5. Ejecutar `doctor` en ese perfil: verifica conexión autenticada y punto, sin emitir. Que funcione WSAA o FEDummy no prueba autorización sobre el representado. Si ARCA rechaza la relación, corregirla en el portal; nunca reemplazar el CUIT para que pase.
6. Configurar una conexión MCP de nombre claro para ese perfil con `ARCA_CODEX_HOME` apuntando a la carpeta base privada y `ARCA_CODEX_PROFILE` al nombre. El proceso conserva ese emisor; seleccionar expresamente su conexión antes de preparar. Una lista de perfiles no cambia el emisor actual. No registrar certificados o contraseñas en la configuración MCP, solo rutas/nombres no secretos.
7. Para usar un certificado autorizado ya existente, usar `arca-codex --profile cliente-homo setup --existing` e ingresar rutas y contraseña únicamente en la terminal local; no copiar secretos por chat. Mantener el llavero del sistema. El asistente de nuevas credenciales no sobrescribe claves existentes. Revisar localmente correspondencia y CUIT de titular.
8. Requerir homologación por combinación de régimen/tipo de comprobante y revisión del PDF antes de habilitar producción con intervención local del usuario. No habilitar producción automáticamente ni emitir comprobantes para «probar» una relación.
9. Mostrar el emisor correcto en cada confirmación fiscal, PDF, carpeta y propuesta de correo. Verificar también la cuenta Gmail: cuenta emisora del correo y emisor fiscal son conceptos diferentes. No asumir que cambiar de CUIT cambia la cuenta conectada.
10. Para monotributo consolidar ingresos de todos los puntos/perfiles del mismo CUIT mediante importación revisada; `revenue_status` solo ve su journal y su historial importado. No afirmar consolidación automática entre carpetas.

Fuentes: [WSAA y certificados](https://www.afip.gob.ar/ws/WSAA/), [manual de certificados de producción](https://www.afip.gob.ar/ws/WSAA/wsaa_obtener_certificado_produccion.pdf). Seguir el portal vigente para las relaciones; no compartir claves fiscales de terceros.
