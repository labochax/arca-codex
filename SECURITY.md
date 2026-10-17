# Seguridad y privacidad

- No adjuntar certificados, claves, `.env`, tickets WSAA, bases del diario ni facturas
  reales a issues, pull requests o ejemplos. El repo público solo lleva código y datos sintéticos.
- La clave se genera en el equipo, se cifra como PKCS#8 y se descifra en memoria.
  La contraseña aleatoria queda en el llavero seguro del sistema. Se rechazan backends
  de llavero en texto plano. Certificado y CSR no son claves privadas, pero tampoco
  se incluyen en el repo.
- Tickets WSAA: cache en llavero por ambiente, CUIT, fingerprint del certificado y
  servicio; se renuevan cerca de su vencimiento. No se escriben en logs ni respuestas MCP.
- Los archivos de facturación son privados por permisos, **no están cifrados por la
  aplicación**. Protegé el disco, la sesión y los respaldos. En Windows revisá las ACL.
- El servidor solo implementa stdio: no abre puertos, no tiene un endpoint remoto ni
  autenticación multiusuario. No lo publiques directamente en Internet.
- TLS se verifica y no se siguen redirecciones. Los destinos SOAP están fijos en código.
  No hay retry automático de autorizaciones. XML externo se procesa con defusedxml.
- El hash detecta modificaciones accidentales y vincula la confirmación a los datos;
  no sustituye la aprobación humana ni protege de alguien que controla tu cuenta local.
- El bloqueo y la idempotencia son locales. No copies una instalación operativa para
  emitir en paralelo desde otro equipo con el mismo punto de venta.
- Ante un posible secreto publicado: revocá/renová la credencial con ARCA, luego
  retirala del historial Git. Borrar solo el archivo del último commit no alcanza.
- Para informar un problema, incluí pasos reproducibles con datos sintéticos y códigos
  de error. No publiques un exploit que exponga datos de una instalación real.

Este software no constituye asesoramiento tributario. La habilitación en producción
requiere prueba autenticada y revisión del comprobante y encuadramiento fiscal.
