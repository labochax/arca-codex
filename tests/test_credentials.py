import base64
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

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


def test_import_existing_pair_reencrypts_and_preserves_originals(tmp_path, monkeypatch):
    from datetime import UTC, datetime, timedelta

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from arca_codex.credentials import import_pair

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [
            x509.NameAttribute(NameOID.COMMON_NAME, "REPRESENTANTE DE PRUEBA"),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, "CUIT 20123456786"),
        ]
    )
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(123)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    source = tmp_path / "source"
    source.mkdir()
    keyfile, certfile = source / "key.pem", source / "cert.pem"
    raw = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    keyfile.write_bytes(raw)
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    class Backend:
        values = {}

        def get_password(self, service, account):
            return self.values.get((service, account))

        def set_password(self, service, account, value):
            self.values[service, account] = value

    backend = Backend()
    monkeypatch.setattr("arca_codex.credentials.secure_keyring", lambda: backend)
    root = tmp_path / "profile"
    result = import_pair(root, "20123456786", "homologacion", certfile, keyfile, "")
    assert keyfile.read_bytes() == raw
    encrypted = Path(result["encrypted_key_path"]).read_bytes()
    assert b"BEGIN ENCRYPTED PRIVATE KEY" in encrypted
    assert encrypted != raw
    with pytest.raises(ValueError, match="no se sobrescriben"):
        import_pair(root, "20123456786", "homologacion", certfile, keyfile, "")
    with pytest.raises(ValueError, match="CUIT"):
        import_pair(tmp_path / "other", "30712345671", "homologacion", certfile, keyfile, "")


def test_wsaa_shared_certificate_cache_keeps_represented_cuit(tmp_path, profile, monkeypatch):
    import json
    from types import SimpleNamespace

    backend = MemoryKeyring()
    cert = SimpleNamespace(fingerprint=lambda algorithm: b"test-fingerprint")
    monkeypatch.setattr(soap, "load_pair", lambda p: (None, cert))
    monkeypatch.setattr(soap, "secure_keyring", lambda: backend)

    def never_post(*args):
        raise AssertionError("A valid cached ticket must not trigger WSAA")

    monkeypatch.setattr(soap, "post", never_post)
    fingerprint = b"test-fingerprint".hex()
    legacy = f"ticket:homologacion:{profile.issuer.cuit}:{fingerprint}:wsfe"
    backend.set_password(
        SERVICE,
        legacy,
        json.dumps(
            {
                "token": "TEST",
                "sign": "SIGN",
                "expirationTime": (datetime.now(UTC) + timedelta(hours=2)).isoformat(),
            }
        ),
    )
    first = Wsaa(profile, tmp_path).ticket()
    other = profile.model_copy(deep=True)
    other.issuer.cuit = "30712345671"
    second = Wsaa(other, tmp_path / "other").ticket()
    assert first["Token"] == second["Token"] == "TEST"
    assert first["Cuit"] == 20123456786
    assert second["Cuit"] == 30712345671
    assert backend.get_password(SERVICE, legacy)
    assert len(backend.items) == 2
