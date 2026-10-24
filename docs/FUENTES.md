# Fuentes y decisión de implementación

Consulta: 16/10/2026. Enlaces oficiales como referencia; revisar cambios antes de
modificar reglas fiscales o habilitar otros comprobantes.

| Tema | Fuente |
|---|---|
| Catálogo y documentación de WS | https://www.afip.gob.ar/ws/ |
| WSFEv1, manual vigente | https://www.afip.gob.ar/ws/documentacion/ws-factura-electronica.asp |
| Manual revisado, v4.7 / 01-09-2026 | https://www.afip.gob.ar/ws/documentacion/manuales/manual-desarrollador-ARCA-COMPG.pdf |
| Contrato SOAP homologación | https://wswhomo.afip.gov.ar/wsfev1/service.asmx?WSDL |
| WSAA, certificados y relaciones | https://www.afip.gov.ar/ws/documentacion/wsaa.asp |
| Certificados de prueba y producción | https://www.afip.gob.ar/ws/documentacion/certificados.asp |
| Punto de venta por modalidad | https://ftp.afip.gob.ar/fe/emision-autorizacion/solicitud-autorizacion.asp |
| QR | https://www.afip.gob.ar/fe/qr/ |
| Revisión de representación 24/10/2026 | [Fuentes, controles y límites](REPRESENTACION.md) |
| Contexto TLS explícito de HTTPX | https://www.python-httpx.org/advanced/ssl/ |
| Política TLS y suites de Python | https://docs.python.org/3.12/library/ssl.html |
| Nivel de seguridad OpenSSL | https://docs.openssl.org/3.0/man3/SSL_CTX_set_security_level/ |
| Plugins de Codex | https://developers.openai.com/plugins/build/plugins |
| MCP para Codex | https://developers.openai.com/codex/mcp |
| SDK MCP Python | https://github.com/modelcontextprotocol/python-sdk |

## Proyectos y proveedores revisados

- [Yoryoboy/ArcaMCP](https://github.com/Yoryoboy/ArcaMCP): MCP MIT con onboarding,
  facturación y PDF; depende de `@afipsdk/afip.js` y documenta un token del proveedor.
  No es una integración independiente de AfipSDK. No se copió código.
- [AfipSDK](https://docs.afipsdk.com/siguientes-pasos/web-services/factura-electronica):
  documentación y abstracción de servicios; incorpora servicios del proveedor.
  No es el SDK oficial de ARCA.
- [KeyCAE](https://keycae.ar/blog/mcp-facturacion-argentina.html): MCP y API alojada;
  custodia credenciales en su infraestructura y ofrece planes de facturación.
- [devpablocristo/arca-facturacion](https://github.com/devpablocristo/arca-facturacion):
  SDK TypeScript directo con servicios adicionales; alternativa a evaluar si se
  amplía el alcance. No se copió código ni se agregó como dependencia.
- [GeraCollante/PyARCA](https://github.com/GeraCollante/PyARCA): CLI Python derivada
  de pyafipws, útil como antecedente. No se incorporó código de ese proyecto.

Se eligió un proyecto propio, limitado a C/FCE C de servicios y conectado por SOAP
directamente a ARCA. Así el código publicado y las credenciales particulares quedan
separados y no se impone una suscripción de facturación. El costo del cliente Codex,
la computadora y cualquier hosting que alguien agregue sigue siendo independiente.
