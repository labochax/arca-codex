import ssl
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import certifi
import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from arca_codex import soap


def test_tls_policy_is_a_subset_of_defaults():
    default = ssl.create_default_context(cafile=certifi.where())
    context = soap.tls_context()
    before = {cipher["id"]: cipher for cipher in default.get_ciphers()}
    after = {cipher["id"]: cipher for cipher in context.get_ciphers()}

    assert after.keys() <= before.keys()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= max(default.minimum_version, ssl.TLSVersion.TLSv1_2)
    assert context.security_level >= max(default.security_level, 2)
    assert context.options == default.options
    assert context.maximum_version == default.maximum_version
    assert context.get_ca_certs(binary_form=True) == default.get_ca_certs(binary_form=True)
    assert not any(c["kea"] == "kx-dhe" or "DHE" in c["name"].split("-") for c in after.values())
    assert {c["id"] for c in before.values() if c["kea"] == "kx-ecdhe"} <= after.keys()
    assert {c["id"] for c in before.values() if c["protocol"] == "TLSv1.3"} == {
        c["id"] for c in after.values() if c["protocol"] == "TLSv1.3"
    }


@pytest.fixture
def synthetic_tls(tmp_path):
    """Ephemeral test CA and localhost certificate; never uses a fiscal profile."""
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = (tmp_path / name for name in ("ca.pem", "cert.pem", "key.pem"))
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    return ca_path, server


def handshake(client_context, server_context, hostname="localhost"):
    """Negotiate real OpenSSL TLS in memory, with no network dependency."""
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    complete = [False, False]
    for _ in range(20):
        for index, peer in enumerate((client, server)):
            if not complete[index]:
                try:
                    peer.do_handshake()
                    complete[index] = True
                except ssl.SSLWantReadError:
                    pass
        if client_out.pending:
            server_in.write(client_out.read())
        if server_out.pending:
            client_in.write(server_out.read())
        if all(complete):
            return client
    pytest.fail("TLS handshake did not finish")


@pytest.mark.parametrize("version", [ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3])
def test_tls_handshake_uses_certifi_ca_and_preserves_ecdhe_and_tls13(
    monkeypatch, synthetic_tls, version
):
    ca_path, server = synthetic_tls
    monkeypatch.setattr(soap.certifi, "where", lambda: str(ca_path))
    context = soap.tls_context()
    server.minimum_version = server.maximum_version = version
    if version == ssl.TLSVersion.TLSv1_2:
        server.set_ciphers("ECDHE-RSA-AES256-GCM-SHA384")
    peer = handshake(context, server)
    assert peer.version() == version.name.replace("_", ".", 1)
    if version == ssl.TLSVersion.TLSv1_2:
        assert peer.cipher()[0] == "ECDHE-RSA-AES256-GCM-SHA384"


def test_tls_rejects_untrusted_certificate(synthetic_tls):
    _, server = synthetic_tls
    with pytest.raises(ssl.SSLCertVerificationError):
        handshake(soap.tls_context(), server)


def test_tls_rejects_wrong_hostname(monkeypatch, synthetic_tls):
    ca_path, server = synthetic_tls
    monkeypatch.setattr(soap.certifi, "where", lambda: str(ca_path))
    with pytest.raises(ssl.SSLCertVerificationError) as caught:
        handshake(soap.tls_context(), server, hostname="wrong.example.invalid")
    assert caught.value.verify_code == 62  # X509_V_ERR_HOSTNAME_MISMATCH


@pytest.mark.parametrize(
    "policy",
    [
        {"security_level": 1},
        {"verify_mode": ssl.CERT_NONE},
        {"check_hostname": False},
    ],
)
def test_tls_fails_closed_if_default_policy_is_unsafe(monkeypatch, policy):
    attributes = dict(
        minimum_version=ssl.TLSVersion.TLSv1_2,
        security_level=2,
        verify_mode=ssl.CERT_REQUIRED,
        check_hostname=True,
    )
    attributes.update(policy)
    monkeypatch.setattr(
        soap.ssl, "create_default_context", lambda **kwargs: SimpleNamespace(**attributes)
    )
    with pytest.raises(soap.TransportError, match="política de seguridad"):
        soap.tls_context()


def test_tls_fails_closed_without_compatible_default_ciphers(monkeypatch):
    context = SimpleNamespace(
        minimum_version=ssl.TLSVersion.TLSv1_2,
        security_level=2,
        verify_mode=ssl.CERT_REQUIRED,
        check_hostname=True,
        get_ciphers=lambda: [
            {"name": "DHE-RSA-AES256-GCM-SHA384", "kea": "kx-dhe", "protocol": "TLSv1.2"}
        ],
    )
    monkeypatch.setattr(soap.ssl, "create_default_context", lambda **kwargs: context)
    with pytest.raises(soap.TransportError, match="No hay cifrados"):
        soap.tls_context()


@pytest.mark.parametrize("fail", [False, True])
def test_post_uses_explicit_context_timeout_and_one_attempt(monkeypatch, fail):
    real_context = soap.tls_context()
    monkeypatch.setattr(soap, "tls_context", lambda: real_context)
    options, requests = [], []
    secret = "synthetic-secret-must-stay-hidden"

    class Client:
        def __init__(self, **kwargs):
            options.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            requests.append((url, kwargs))
            if fail:
                raise httpx.ConnectError(secret)
            return SimpleNamespace(content=b"<Envelope/>", status_code=200)

    monkeypatch.setattr(soap.httpx, "Client", Client)
    if fail:
        with pytest.raises(soap.TransportError) as caught:
            soap.post("https://soap.example.invalid", "operation", b"synthetic-body")
        assert str(caught.value) == "No se obtuvo una respuesta válida de ARCA"
        assert secret not in str(caught.value)
        assert isinstance(caught.value.__cause__, httpx.ConnectError)
    else:
        assert (
            soap.post("https://soap.example.invalid", "operation", b"synthetic-body").tag
            == "Envelope"
        )
    assert options == [
        dict(verify=real_context, timeout=30, follow_redirects=False, trust_env=False)
    ]
    assert requests == [
        (
            "https://soap.example.invalid",
            dict(
                content=b"synthetic-body",
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"operation"'},
            ),
        )
    ]
