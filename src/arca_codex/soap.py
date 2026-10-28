from __future__ import annotations

import base64
import json
import ssl
import time
from datetime import UTC, datetime, timedelta
from xml.etree import ElementTree as ET

import certifi
import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.serialization import pkcs7
from defusedxml.ElementTree import fromstring
from filelock import FileLock

from .credentials import SERVICE, load_pair, secure_keyring

SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
FE = "http://ar.gov.afip.dif.FEV1/"
AA = "http://wsaa.view.sua.dvadac.desein.afip.gov"
ENDPOINTS = {
    "homologacion": (
        "https://wsaahomo.afip.gov.ar/ws/services/LoginCms",
        "https://wswhomo.afip.gov.ar/wsfev1/service.asmx",
    ),
    "produccion": (
        "https://wsaa.afip.gov.ar/ws/services/LoginCms",
        "https://servicios1.afip.gov.ar/wsfev1/service.asmx",
    ),
}


class ArcaError(RuntimeError):
    def __init__(self, message, codes=()):
        super().__init__(message)
        self.codes = tuple(codes)


class TransportError(ArcaError):
    """A write may have reached ARCA. Never retry an authorization automatically."""


def local(tag):
    return tag.rsplit("}", 1)[-1]


def unpack(node):
    if len(node) == 0:
        return node.text or ""
    d = {}
    for c in node:
        k, v = local(c.tag), unpack(c)
        if k in d:
            if not isinstance(d[k], list):
                d[k] = [d[k]]
            d[k].append(v)
        else:
            d[k] = v
    return d


def many(value):
    if value is None or value == "":
        return []
    return value if isinstance(value, list) else [value]


def fill(parent, data, namespace):
    for k, v in data.items():
        for item in v if isinstance(v, list) else [v]:
            child = ET.SubElement(parent, f"{{{namespace}}}{k}" if namespace else k)
            if isinstance(item, dict):
                fill(child, item, namespace)
            else:
                child.text = str(item)


def envelope(method, params, namespace=FE):
    root = ET.Element(f"{{{SOAP}}}Envelope")
    body = ET.SubElement(root, f"{{{SOAP}}}Body")
    op = ET.SubElement(body, f"{{{namespace}}}{method}")
    fill(op, params, FE if namespace == FE else "")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def tls_context() -> ssl.SSLContext:
    """Keep default TLS policy and CAs, excluding finite-field DHE only.

    Some ARCA servers prefer DHE parameters rejected by OpenSSL security level 2.
    Explicit cipher names from the default context avoid broadening its policy.
    set_ciphers does not change TLS 1.3 suites.
    """
    context = ssl.create_default_context(cafile=certifi.where())
    if context.minimum_version < ssl.TLSVersion.TLSv1_2:
        context.minimum_version = ssl.TLSVersion.TLSv1_2
    if (
        context.security_level < 2
        or context.verify_mode != ssl.CERT_REQUIRED
        or not context.check_hostname
    ):
        raise TransportError("El contexto TLS no cumple la política de seguridad requerida")
    ciphers = [
        cipher["name"]
        for cipher in context.get_ciphers()
        if cipher["protocol"] != "TLSv1.3"
        and cipher.get("kea") != "kx-dhe"
        and "DHE" not in cipher["name"].split("-")
    ]
    if not ciphers:
        raise TransportError("No hay cifrados TLS predeterminados compatibles sin DHE")
    context.set_ciphers(":".join(ciphers))
    return context


def post(url, action, body):
    try:
        # No redirection, SOAP logging, proxy inheritance, or automatic retry.
        with httpx.Client(
            verify=tls_context(), timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            res = client.post(
                url,
                content=body,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{action}"'},
            )
        if len(res.content) > 4_000_000:
            raise TransportError("Respuesta de ARCA demasiado grande")
        root = fromstring(res.content)
        if any(local(x.tag) == "Fault" for x in root.iter()):
            raise ArcaError(
                "ARCA devolvió un SOAP Fault. Revisá certificado, autorización y ambiente."
            )
        if res.status_code != 200:
            raise TransportError(f"Respuesta HTTP inesperada de ARCA ({res.status_code})")
        return root
    except ArcaError:
        raise
    except Exception as exc:
        # Do not propagate request/XML/credentials in error strings.
        raise TransportError("No se obtuvo una respuesta válida de ARCA") from exc


class Wsaa:
    def __init__(self, profile, root):
        self.profile, self.root = profile, root

    def ticket(self):
        with FileLock(str(self.root / "wsaa.lock"), timeout=45):
            key, cert = load_pair(self.profile)
            fingerprint = cert.fingerprint(hashes.SHA256()).hex()
            cache_id = (
                f"ticket:{self.profile.environment}:{self.profile.issuer.cuit}:{fingerprint}:wsfe"
            )
            backend = secure_keyring()
            cached = backend.get_password(SERVICE, cache_id)
            if cached:
                ticket = json.loads(cached)
                if datetime.fromisoformat(ticket["expirationTime"]) > datetime.now(UTC) + timedelta(
                    minutes=3
                ):
                    return {
                        "Token": ticket["token"],
                        "Sign": ticket["sign"],
                        "Cuit": int(self.profile.issuer.cuit),
                    }
            now = datetime.now(UTC)
            tra = ET.Element("loginTicketRequest", version="1.0")
            h = ET.SubElement(tra, "header")
            for name, value in {
                "uniqueId": str(int(time.time())),
                "generationTime": (now - timedelta(minutes=5)).isoformat(),
                "expirationTime": (now + timedelta(hours=12)).isoformat(),
            }.items():
                ET.SubElement(h, name).text = value
            ET.SubElement(tra, "service").text = "wsfe"
            signed = (
                pkcs7.PKCS7SignatureBuilder()
                .set_data(ET.tostring(tra, encoding="utf-8"))
                .add_signer(cert, key, hashes.SHA256())
                .sign(serialization.Encoding.DER, [pkcs7.PKCS7Options.Binary])
            )
            root = post(
                ENDPOINTS[self.profile.environment][0],
                "",
                envelope("loginCms", {"in0": base64.b64encode(signed).decode()}, AA),
            )
            encoded = next((n.text for n in root.iter() if local(n.tag) == "loginCmsReturn"), None)
            if not encoded:
                raise ArcaError("WSAA no devolvió un ticket de acceso")
            ticket_xml = fromstring(encoded)
            fields = {local(n.tag): n.text for n in ticket_xml.iter()}
            ticket = {k: fields[k] for k in ("token", "sign", "expirationTime")}
            backend.set_password(SERVICE, cache_id, json.dumps(ticket))
            return {
                "Token": ticket["token"],
                "Sign": ticket["sign"],
                "Cuit": int(self.profile.issuer.cuit),
            }


class Wsfe:
    def __init__(self, profile, root):
        self.profile = profile
        self.auth = Wsaa(profile, root)

    def call(self, method, params=None, authenticate=True):
        payload = {"Auth": self.auth.ticket()} if authenticate else {}
        payload.update(params or {})
        root = post(ENDPOINTS[self.profile.environment][1], FE + method, envelope(method, payload))
        node = next((n for n in root.iter() if local(n.tag) == method + "Result"), None)
        if node is None:
            raise TransportError("Respuesta ARCA sin resultado esperado")
        result = unpack(node)
        if not isinstance(result, dict):
            raise TransportError("Respuesta ARCA incompleta")
        errors = (
            many(result.get("Errors", {}).get("Err"))
            if isinstance(result.get("Errors"), dict)
            else []
        )
        if errors:
            codes = [str(x.get("Code", "?")) for x in errors]
            # Return codes, not arbitrary server text that could include private fields.
            raise ArcaError("ARCA rechazó la consulta: códigos " + ", ".join(codes), codes)
        return result

    def health(self):
        return self.call("FEDummy", authenticate=False)

    def points(self):
        try:
            result = self.call("FEParamGetPtosVenta")
        except ArcaError as exc:
            # Homologation can answer the last-number query while its catalog is
            # unavailable. An empty catalog is not proof that a point is enabled.
            if self.profile.environment == "homologacion" and exc.codes == ("602",):
                return []
            raise
        return many(result.get("ResultGet", {}).get("PtoVenta"))

    def iva_conditions(self, voucher_class="C"):
        return many(
            self.call("FEParamGetCondicionIvaReceptor", {"ClaseCmp": voucher_class})
            .get("ResultGet", {})
            .get("CondicionIvaReceptor")
        )

    def vat_rates(self):
        return many(self.call("FEParamGetTiposIva").get("ResultGet", {}).get("IvaTipo"))

    def last(self, voucher_type):
        result = self.call(
            "FECompUltimoAutorizado",
            {"PtoVta": self.profile.point_of_sale, "CbteTipo": voucher_type},
        )
        try:
            point, kind, number = (int(result[k]) for k in ("PtoVta", "CbteTipo", "CbteNro"))
        except (KeyError, TypeError, ValueError) as exc:
            raise TransportError("Consulta de último comprobante incompleta") from exc
        if point != self.profile.point_of_sale or kind != voucher_type or number < 0:
            raise TransportError("Consulta de último comprobante no coincide con lo solicitado")
        return number

    def consult(self, voucher_type, number):
        try:
            r = self.call(
                "FECompConsultar",
                {
                    "FeCompConsReq": {
                        "CbteTipo": voucher_type,
                        "CbteNro": number,
                        "PtoVta": self.profile.point_of_sale,
                    }
                },
            )
            return r.get("ResultGet") or None
        except ArcaError as exc:
            if exc.codes == ("602",):
                return None
            raise

    def authorize(self, voucher_type, detail):
        return self.call(
            "FECAESolicitar",
            {
                "FeCAEReq": {
                    "FeCabReq": {
                        "CantReg": 1,
                        "PtoVta": self.profile.point_of_sale,
                        "CbteTipo": voucher_type,
                    },
                    "FeDetReq": {"FECAEDetRequest": detail},
                }
            },
        )
