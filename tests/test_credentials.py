import base64
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from defusedxml.ElementTree import fromstring

from arca_codex import credentials, soap
from arca_codex.credentials import SERVICE, generate, load_pair
from arca_codex.soap import Wsaa, local


class MemoryKeyring:
    def __init__(self):
        self.items = {}

    def get_password(self, service, account):
        return self.items.get((service, account))

    def set_password(self, service, account, value):
        self.items[service, account] = value


def test_secure_backend_rejects_plaintext(monkeypatch):
    monkeypatch.setattr(credentials.keyring, "get_keyring", lambda: MemoryKeyring())
    with pytest.raises(ValueError, match="llavero seguro"):
        credentials.secure_keyring()


def test_local_key_encryption_csr_and_wsaa_signature(tmp_path, profile, monkeypatch):
    backend = MemoryKeyring()
    monkeypatch.setattr(credentials, "secure_keyring", lambda: backend)
    monkeypatch.setattr(soap, "secure_keyring", lambda: backend)
    paths = generate(tmp_path, profile.issuer.cuit, "homologacion", "TEST ONLY")
    password = backend.get_password(SERVICE, paths["keyring_account"])
    encrypted = (tmp_path / "credentials/homologacion/20123456786/private-key.pem").read_bytes()
    assert b"BEGIN ENCRYPTED PRIVATE KEY" in encrypted
    assert password.encode() not in encrypted
    key = serialization.load_pem_private_key(encrypted, password.encode())
    csr = x509.load_pem_x509_csr(
        (tmp_path / "credentials/homologacion/20123456786/request.csr").read_bytes()
    )
    assert csr.is_signature_valid
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(csr.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    from pathlib import Path

    Path(paths["certificate_path"]).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    p = profile.model_copy(update={k: v for k, v in paths.items() if k != "csr_path"})
    assert load_pair(p)[1].serial_number == cert.serial_number
    with pytest.raises(ValueError, match="Ya existen"):
        generate(tmp_path, profile.issuer.cuit, "homologacion", "TEST ONLY")
    calls = []

    def response(url, action, body):
        calls.append(body)
        cms = next(n.text for n in fromstring(body).iter() if local(n.tag) == "in0")
        cms_path = tmp_path / "signed.der"
        cms_path.write_bytes(base64.b64decode(cms))
        # Independently verify CMS and recover the signed XML; never contacts ARCA.
        import shutil

        if shutil.which("openssl"):
            result = subprocess.run(
                [
                    "openssl",
                    "cms",
                    "-verify",
                    "-inform",
                    "DER",
                    "-in",
                    str(cms_path),
                    "-noverify",
                    "-binary",
                ],
                capture_output=True,
                check=True,
            )
            tra = fromstring(result.stdout)
            assert tra.findtext("service") == "wsfe"
            assert datetime.fromisoformat(tra.findtext("header/expirationTime")) > now
        xml = (
            "<loginTicketResponse><header><expirationTime>"
            + (now + timedelta(hours=12)).isoformat()
            + "</expirationTime></header><credentials><token>TEST_TOKEN</token>"
            "<sign>TEST_SIGN</sign></credentials></loginTicketResponse>"
        )
        import xml.etree.ElementTree as ET

        root = ET.Element("response")
        ET.SubElement(root, "loginCmsReturn").text = xml
        return root

    monkeypatch.setattr(soap, "post", response)
    auth = Wsaa(p, tmp_path)
    a = auth.ticket()
    b = auth.ticket()
    assert a == b == {"Token": "TEST_TOKEN", "Sign": "TEST_SIGN", "Cuit": 20123456786}
    assert len(calls) == 1
    assert all(b"TEST_TOKEN" not in f.read_bytes() for f in tmp_path.rglob("*") if f.is_file())
