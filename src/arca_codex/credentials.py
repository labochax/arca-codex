from __future__ import annotations

import hashlib
import secrets
from datetime import UTC, datetime
from pathlib import Path

import keyring
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from .models import cuit_valid
from .storage import write_private

SERVICE = "arca-codex"


def secure_keyring():
    backend = keyring.get_keyring()
    allowed = (
        "keyring.backends.Windows",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
    )
    if not type(backend).__module__.startswith(allowed):
        raise ValueError(
            "Se requiere el llavero seguro del sistema (Windows/macOS/SecretService). "
            "No se admite un backend de texto plano. Ver docs/CONFIGURACION.md."
        )
    return backend


def credential_account(root, environment, cuit):
    legacy = f"{environment}:{cuit}"
    marker = root / "credentials" / environment / cuit / "keyring-account.txt"
    if marker.exists():
        return marker.read_text().strip()
    # Existing CSRs use the old account. Never rotate them by upgrading.
    if (marker.parent / "request.csr").exists():
        return legacy
    return legacy + ":" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]


def generate(root: Path, cuit: str, environment: str, common_name: str) -> dict:
    cuit_valid(cuit)
    if environment not in ("homologacion", "produccion"):
        raise ValueError("Ambiente inválido")
    folder = root / "credentials" / environment / cuit
    key_path, csr_path = folder / "private-key.pem", folder / "request.csr"
    account = credential_account(root, environment, cuit)
    backend = secure_keyring()
    if key_path.exists() or backend.get_password(SERVICE, account):
        raise ValueError("Ya existen credenciales; no se sobrescriben. Respaldá antes de rotar.")
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    password = secrets.token_urlsafe(48)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "AR"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, common_name),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"CUIT {cuit}"),
        ]
    )
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    # Set the secret first: if disk writing fails, do not silently lose decryption material.
    backend.set_password(SERVICE, account, password)
    write_private(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(password.encode()),
        ),
    )
    write_private(csr_path, csr.public_bytes(serialization.Encoding.PEM))
    write_private(folder / "keyring-account.txt", account.encode())
    return {
        "csr_path": str(csr_path),
        "encrypted_key_path": str(key_path),
        "certificate_path": str(folder / "certificate.pem"),
        "keyring_account": account,
    }


def load_pair(profile):
    try:
        password = secure_keyring().get_password(SERVICE, profile.keyring_account)
        if not password:
            raise ValueError("No está la contraseña de la clave en el llavero del sistema")
        key = serialization.load_pem_private_key(
            Path(profile.encrypted_key_path).read_bytes(), password.encode()
        )
        cert = x509.load_pem_x509_certificate(Path(profile.certificate_path).read_bytes())
    except (OSError, TypeError) as exc:
        raise ValueError("No se pudieron cargar las credenciales locales") from exc
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError("La clave debe ser RSA")
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if key.public_key().public_bytes(*fmt) != cert.public_key().public_bytes(*fmt):
        raise ValueError("El certificado no corresponde a la clave privada")
    if getattr(profile, "certificate_cuit", None):
        import re

        serials = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
        identifiers = [re.sub(r"[^0-9]", "", item.value) for item in serials]
        if profile.certificate_cuit not in identifiers:
            raise ValueError("El CUIT del certificado no coincide con su titular configurado")
    now = datetime.now(UTC)
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
        raise ValueError("Certificado vencido o aún no vigente")
    return key, cert


def import_pair(root, cuit, environment, certificate_path, key_path, password):
    """Import an existing pair only through the local assistant; always encrypt our copy."""
    import re

    certificate = x509.load_pem_x509_certificate(Path(certificate_path).expanduser().read_bytes())
    private_key = serialization.load_pem_private_key(
        Path(key_path).expanduser().read_bytes(), password.encode() if password else None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey) or private_key.key_size < 2048:
        raise ValueError("Se requiere clave RSA de al menos 2048 bits")
    fmt = (serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    if private_key.public_key().public_bytes(*fmt) != certificate.public_key().public_bytes(*fmt):
        raise ValueError("El certificado no corresponde a la clave privada")
    if cuit not in [
        re.sub(r"[^0-9]", "", a.value)
        for a in certificate.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
    ]:
        raise ValueError("El CUIT del certificado no coincide con su titular configurado")
    now = datetime.now(UTC)
    if not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc:
        raise ValueError("Certificado vencido o aún no vigente")
    folder = root / "credentials" / environment / cuit
    if any(
        (folder / name).exists() for name in ("private-key.pem", "certificate.pem", "request.csr")
    ):
        raise ValueError("El perfil ya tiene credenciales; no se sobrescriben")
    account = credential_account(root, environment, cuit)
    backend = secure_keyring()
    if backend.get_password(SERVICE, account):
        raise ValueError("El perfil ya tiene credenciales en el llavero")
    secret = secrets.token_urlsafe(48)
    encrypted = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(secret.encode()),
    )
    backend.set_password(SERVICE, account, secret)
    write_private(folder / "private-key.pem", encrypted)
    write_private(folder / "certificate.pem", certificate.public_bytes(serialization.Encoding.PEM))
    write_private(folder / "keyring-account.txt", account.encode())
    return {
        "certificate_path": str(folder / "certificate.pem"),
        "encrypted_key_path": str(folder / "private-key.pem"),
        "keyring_account": account,
    }
