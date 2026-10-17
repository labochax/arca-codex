from __future__ import annotations

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


def generate(root: Path, cuit: str, environment: str, common_name: str) -> dict:
    cuit_valid(cuit)
    if environment not in ("homologacion", "produccion"):
        raise ValueError("Ambiente inválido")
    folder = root / "credentials" / environment / cuit
    key_path, csr_path = folder / "private-key.pem", folder / "request.csr"
    account = f"{environment}:{cuit}"
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
    now = datetime.now(UTC)
    if not cert.not_valid_before_utc <= now < cert.not_valid_after_utc:
        raise ValueError("Certificado vencido o aún no vigente")
    return key, cert
