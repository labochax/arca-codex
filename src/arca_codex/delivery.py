"""Presentation and delivery only: no fiscal requests and no email transport."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
from calendar import monthrange
from datetime import date
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path

from filelock import FileLock

from .models import digest
from .storage import write_private

MONTHS = ("", "ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def filing_period(summary: dict) -> str:
    start = date.fromisoformat(summary["invoice"]["service_from"])
    end = date.fromisoformat(summary["invoice"]["service_to"])
    if start.year == end.year and start.month == end.month and start.day == 1:
        if end.day == monthrange(end.year, end.month)[1]:
            return start.strftime("%Y-%m")
    return f"{start.isoformat()}_al_{end.isoformat()}"


def period_prefix(period: str) -> str:
    """Only accept dates, never user-supplied path fragments or invoice identifiers."""
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}", period):
        value = date.fromisoformat(period + "-01")
        return f"{period} - {MONTHS[value.month]}"
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", period):
        return date.fromisoformat(period).isoformat()
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}_al_[0-9]{4}-[0-9]{2}-[0-9]{2}", period):
        start, end = (date.fromisoformat(v) for v in period.split("_al_"))
        if start <= end:
            return period
    raise ValueError("Fecha de archivo: usar AAAA-MM, AAAA-MM-DD o AAAA-MM-DD_al_AAAA-MM-DD")


def pdf_filename(summary: dict, *, period: str | None = None, draft: bool = False) -> str:
    prefix = period_prefix(period if period is not None else filing_period(summary))
    cuit = summary["issuer"]["cuit"]
    kind, point, number = (
        summary["invoice"]["voucher_type"],
        summary["point_of_sale"],
        summary.get("number", 0),
    )
    if not re.fullmatch(r"[0-9]{11}", cuit) or kind not in (11, 211):
        raise ValueError("Identificación inválida para el nombre del PDF")
    if not 1 <= point <= 99999 or not 0 <= number <= 99999999:
        raise ValueError("Numeración inválida para el nombre del PDF")
    status = "BORRADOR - " if draft else ""
    if summary["environment"] == "homologacion":
        status = "HOMOLOGACION - " + status
    elif summary["environment"] != "produccion":
        raise ValueError("Ambiente desconocido")
    suffix = f"{number:08d}" if number else "SIN-NUMERO-" + digest(summary)[:12]
    return f"{status}{prefix} - {cuit}_{kind:03d}_{point:05d}_{suffix}.pdf"


def output_directory(root: Path, directory: str | None = None) -> Path:
    preferences = root / "delivery-preferences.json"
    if directory is None and preferences.exists():
        data = json.loads(preferences.read_text())
        directory = data.get("default_output_directory")
        if not isinstance(directory, str):
            raise ValueError("Preferencia de carpeta inválida")
    destination = Path(directory).expanduser() if directory is not None else root / "output"
    if not destination.is_absolute():
        raise ValueError("La carpeta de destino debe ser una ruta absoluta de esta computadora")
    destination = destination.resolve()
    if any((parent / ".git").exists() for parent in (destination, *destination.parents)):
        raise ValueError("La carpeta de facturas debe estar fuera de un repositorio Git")
    private_folders = ("credentials", "rendered", "previews", "deliveries", "email-proposals")
    if destination == root.resolve() or any(
        destination.is_relative_to((root / name).resolve()) for name in private_folders
    ):
        raise ValueError("Elegí una carpeta de documentos, no la de credenciales o configuración")
    return destination


def output_options(root: Path, row: dict, *, period=None, directory=None) -> dict:
    summary = row["payload"]
    chosen = period if period is not None else filing_period(summary)
    destination = output_directory(root, directory)
    filename = pdf_filename(summary, period=chosen)
    scope = {
        "draft_id": row["id"],
        "summary_hash": digest(summary),
        "filing_period": chosen,
        "output_directory": str(destination),
        "filename": filename,
    }
    return {
        **scope,
        "default_filing_period": filing_period(summary),
        "directory_exists": destination.is_dir(),
        "directory_will_be_created": destination == (root / "output").resolve()
        and not destination.exists(),
        "confirmation": "GUARDAR " + digest(scope),
        "requires_period_confirmation": True,
        "requires_directory_confirmation": True,
        "questions": [
            "¿Está bien la fecha/período propuesto para el nombre o querés cambiarlo?",
            "¿Guardamos en esta carpeta o querés elegir otra?",
        ],
        "notice": "La fecha de archivo solo cambia el nombre; no modifica la factura.",
    }


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_pdf(
    root: Path,
    row: dict,
    source: Path,
    confirmation: str,
    *,
    period=None,
    directory=None,
    remember_directory=False,
) -> dict:
    options = output_options(root, row, period=period, directory=directory)
    if confirmation != options["confirmation"]:
        raise ValueError("Confirmá la fecha de archivo y la carpeta propuestas antes de guardar")
    destination = Path(options["output_directory"])
    if destination == (root / "output").resolve() and not destination.exists():
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not destination.is_dir():
        raise ValueError("La carpeta no está disponible; se conserva la copia privada del PDF")
    target = destination / options["filename"]
    lock_id = digest(str(target))
    with FileLock(str(root / f"delivery-{lock_id}.lock"), timeout=30):
        content = source.read_bytes()
        existing = target.exists()
        if existing:
            if target.is_symlink() or not target.is_file() or target.read_bytes() != content:
                raise ValueError(
                    "Ya existe un archivo diferente con ese nombre; no se sobrescribió"
                )
        else:
            # Exclusive creation: never replace another program's file, including on Drive for Desktop.
            with target.open("xb") as stream:
                try:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
        receipt = {
            "draft_id": row["id"],
            "summary_hash": digest(row["payload"]),
            "filing_period": options["filing_period"],
            "pdf_path": str(target),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        write_private(root / "deliveries" / f"{row['id']}.json", json.dumps(receipt).encode())
        if remember_directory:
            write_private(
                root / "delivery-preferences.json",
                json.dumps({"default_output_directory": str(destination)}).encode(),
            )
    return {
        **receipt,
        "saved": True,
        "already_present": existing,
        "default_directory_remembered": remember_directory,
        "cloud_sync_verified": False,
        "email_sent": False,
        "next_step": "Ofrecer envío por Gmail; consultar contacto y correo, sin enviarlo todavía.",
    }


def saved_attachment(root: Path, row: dict) -> Path | None:
    receipt = root / "deliveries" / f"{row['id']}.json"
    if not receipt.exists():
        return None
    saved = json.loads(receipt.read_text())
    if saved.get("summary_hash") != digest(row["payload"]):
        raise ValueError("La copia guardada no corresponde al comprobante consultado")
    path = Path(saved["pdf_path"])
    if not path.is_file() or path.is_symlink() or file_hash(path) != saved["sha256"]:
        raise ValueError("La copia guardada falta o cambió; revisala antes de preparar el correo")
    return path


def header_value(value: str) -> str:
    if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("El nombre y los encabezados de correo deben ser texto sin controles")
    return value.strip()


def prepare_email(
    root: Path, row: dict, attachment: Path, name: str, email: str, sender: str | None = None
) -> dict:
    name, email = header_value(name), header_value(email)
    try:
        address = Address(addr_spec=email)
        if not address.username or not address.domain or address.addr_spec != email:
            raise ValueError("invalid")
    except (ValueError, IndexError) as exc:
        raise ValueError("Ingresá un único correo válido, sin listas de destinatarios") from exc
    if sender is not None:
        sender = header_value(sender)
        try:
            account = Address(addr_spec=sender)
            if not account.username or not account.domain or account.addr_spec != sender:
                raise ValueError("invalid")
        except (ValueError, IndexError) as exc:
            raise ValueError("Cuenta remitente inválida") from exc
    summary, inv = row["payload"], row["payload"]["invoice"]
    number = f"{summary['point_of_sale']:05d}-{summary['number']:08d}"
    kind = "FCE MiPyME C" if inv["voucher_type"] == 211 else "Factura C"
    subject = header_value(f"{kind} {number} - {summary['issuer']['name']}")
    start, end = (
        date.fromisoformat(inv[key]).strftime("%d-%m-%Y") for key in ("service_from", "service_to")
    )
    from .pdf import amount

    lines = [
        f"Hola {name},",
        "",
        f"Adjunto la {kind} {number} por el siguiente servicio:",
        inv["description"],
        f"Período: {start} al {end}.",
        f"Importe total: ARS $ {amount(inv['total'])}.",
    ]
    if inv.get("payment_received_on"):
        paid = date.fromisoformat(inv["payment_received_on"]).strftime("%d-%m-%Y")
        lines += [f"Corresponde al pago recibido el {paid}."]
    lines += ["", "Saludos,", summary["issuer"]["name"]]
    body = "\n".join(lines)
    html_body = "<div>" + "<br>".join(html.escape(line) for line in lines) + "</div>"
    content = attachment.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError("El adjunto no es un PDF")
    scope = {
        "draft_id": row["id"],
        "summary_hash": digest(summary),
        "to": email,
        "recipient_name": name,
        "subject": subject,
        "body": body,
        "attachment_filename": attachment.name,
        "attachment_sha256": hashlib.sha256(content).hexdigest(),
        "sender_account": sender,
    }
    proposal = digest(scope)
    payload = {
        "to": email,
        "subject": subject,
        "payload": {
            "mime_type": "multipart/mixed",
            "parts": [
                {
                    "mime_type": "multipart/alternative",
                    "parts": [
                        {"mime_type": "text/plain", "charset": "utf-8", "body": {"content": body}},
                        {
                            "mime_type": "text/html",
                            "charset": "utf-8",
                            "body": {"content": html_body},
                        },
                    ],
                },
                {
                    "mime_type": "application/pdf",
                    "content_disposition": "attachment",
                    "filename": attachment.name,
                    "body": {"base64_url_content": base64.urlsafe_b64encode(content).decode()},
                },
            ],
        },
    }
    message = EmailMessage()
    message["To"] = Address(display_name=name, addr_spec=email)
    message["Subject"] = subject
    if sender:
        payload["from_address"] = sender
        message["From"] = Address(display_name=summary["issuer"]["name"], addr_spec=sender)
    message["X-Unsent"] = "1"
    message.set_content(body)
    message.add_alternative(html_body, subtype="html")
    message.add_attachment(content, maintype="application", subtype="pdf", filename=attachment.name)
    folder = root / "email-proposals"
    # Freeze the reviewed bytes, even when the caller has not archived the invoice yet.
    attachment = folder / proposal / attachment.name
    write_private(attachment, content)
    json_path, eml_path = folder / f"{proposal}.json", folder / f"{proposal}.eml"
    write_private(json_path, json.dumps(payload, ensure_ascii=False).encode())
    write_private(eml_path, message.as_bytes())
    review = {
        "scope": scope,
        "attachment_path": str(attachment),
        "payload_sha256": file_hash(json_path),
    }
    write_private(folder / f"{proposal}.review.json", json.dumps(review).encode())
    return {
        **scope,
        "proposal_id": proposal,
        "attachment_path": str(attachment),
        "gmail_payload_path": str(json_path),
        "eml_path": str(eml_path),
        "sent": False,
        "requires_send_confirmation": True,
        "confirmation": "ENVIAR CORREO " + proposal,
        "requires_sender_account": sender is None,
        "transport": "Gmail connector in the host; not this MCP",
        "next_step": "Verificar cuenta de Gmail y destinatario; mostrar mensaje y adjunto y pedir aprobación. "
        "Usar el conector Gmail solo después de esa aprobación. Si no está disponible, informar y conservar borrador.",
    }


def email_handoff(root: Path, proposal: str, confirmation: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", proposal):
        raise ValueError("Identificador de propuesta inválido")
    if confirmation != "ENVIAR CORREO " + proposal:
        raise ValueError("Confirmación específica de correo requerida")
    folder = root / "email-proposals"
    review = json.loads((folder / f"{proposal}.review.json").read_text())
    scope = review["scope"]
    if digest(scope) != proposal or not scope.get("sender_account"):
        raise ValueError("Verificá la cuenta Gmail y prepará otra propuesta antes de aprobar")
    attachment = Path(review["attachment_path"])
    payload = folder / f"{proposal}.json"
    if (
        not attachment.is_file()
        or attachment.is_symlink()
        or file_hash(attachment) != scope["attachment_sha256"]
        or file_hash(payload) != review["payload_sha256"]
    ):
        raise ValueError("La propuesta o el PDF cambió; preparar y revisar otra propuesta")
    return {
        **scope,
        "gmail_payload_path": str(payload),
        "attachment_path": str(attachment),
        "ready_for_gmail": True,
        "sent": False,
        "notice": "Validación local para entregar al conector Gmail; no es un envío ni prueba de consentimiento humano.",
    }
