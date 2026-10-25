"""Presentation and delivery only: no fiscal requests and no email transport."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import os
import re
from calendar import monthrange
from datetime import UTC, date, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from pathlib import Path
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

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
    private_folders = (
        "credentials",
        "rendered",
        "previews",
        "deliveries",
        "email-proposals",
        "email-deliveries",
    )
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


class EmailRecipient(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=1, max_length=254)


def validated_address(value: str) -> str:
    value = header_value(value)
    try:
        address = Address(addr_spec=value)
        if not address.username or not address.domain or address.addr_spec != value:
            raise ValueError("invalid")
    except (ValueError, IndexError) as exc:
        raise ValueError("Ingresá una única dirección válida por destinatario") from exc
    return value


def email_recipients(recipients, name=None, email=None) -> list[dict]:
    if recipients is not None:
        if name is not None or email is not None:
            raise ValueError("Usá recipients o el destinatario individual, no ambos")
        if not 1 <= len(recipients) <= 100:
            raise ValueError("Indicá entre 1 y 100 destinatarios")
    else:
        recipients = [dict(name=name, email=email)]
    normalized, seen = [], set()
    for value in recipients:
        recipient = EmailRecipient.model_validate(value)
        address = validated_address(recipient.email)
        if address.casefold() in seen:
            raise ValueError("Hay destinatarios duplicados; revisar la lista")
        seen.add(address.casefold())
        normalized.append(dict(name=header_value(recipient.name), email=address))
    return normalized


def recipient_header(recipients: list[dict]) -> str:
    return ", ".join(str(Address(display_name=r["name"], addr_spec=r["email"])) for r in recipients)


def email_payload(scope: dict, content: bytes) -> dict:
    html_body = (
        "<div>" + "<br>".join(html.escape(line) for line in scope["body"].split("\n")) + "</div>"
    )
    payload = {
        "to": scope["to"],
        "subject": scope["subject"],
        "payload": {
            "mime_type": "multipart/mixed",
            "parts": [
                {
                    "mime_type": "multipart/alternative",
                    "parts": [
                        {
                            "mime_type": "text/plain",
                            "charset": "utf-8",
                            "body": {"content": scope["body"]},
                        },
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
                    "filename": scope["attachment_filename"],
                    "body": {"base64_url_content": base64.urlsafe_b64encode(content).decode()},
                },
            ],
        },
    }
    if scope.get("sender_account"):
        payload["from_address"] = scope["sender_account"]
    return payload


def prepare_email(
    root: Path,
    row: dict,
    attachment: Path,
    name: str | None = None,
    email: str | None = None,
    sender: str | None = None,
    *,
    recipients: list[EmailRecipient | dict] | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> dict:
    recipients = email_recipients(recipients, name, email)
    to = recipient_header(recipients)
    if sender is not None:
        sender = validated_address(sender)
    summary, inv = row["payload"], row["payload"]["invoice"]
    number = f"{summary['point_of_sale']:05d}-{summary['number']:08d}"
    kind = "FCE MiPyME C" if inv["voucher_type"] == 211 else "Factura C"
    subject = header_value(
        subject if subject is not None else f"{kind} {number} - {summary['issuer']['name']}"
    )
    start, end = (
        date.fromisoformat(inv[key]).strftime("%d-%m-%Y") for key in ("service_from", "service_to")
    )
    from .pdf import amount

    lines = [
        "Hola " + ", ".join(r["name"] for r in recipients) + ",",
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
    body = body if body is not None else "\n".join(lines)
    if not body.strip() or any((ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in body):
        raise ValueError("El cuerpo debe ser texto sin controles, con saltos de línea LF")
    content = attachment.read_bytes()
    if not content.startswith(b"%PDF-"):
        raise ValueError("El adjunto no es un PDF")
    scope = {
        "draft_id": row["id"],
        "summary_hash": digest(summary),
        "to": to,
        "recipient_name": recipients[0]["name"] if len(recipients) == 1 else None,
        "recipients": recipients,
        "subject": subject,
        "body": body,
        "attachment_filename": attachment.name,
        "attachment_sha256": hashlib.sha256(content).hexdigest(),
        "sender_account": sender,
    }
    proposal = digest(scope)
    payload = email_payload(scope, content)
    message = EmailMessage()
    message["To"] = tuple(Address(display_name=r["name"], addr_spec=r["email"]) for r in recipients)
    message["Subject"] = subject
    if sender:
        message["From"] = Address(display_name=summary["issuer"]["name"], addr_spec=sender)
    message["X-Unsent"] = "1"
    message.set_content(body)
    message.add_alternative(
        payload["payload"]["parts"][0]["parts"][1]["body"]["content"], subtype="html"
    )
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
        "next_step": "Verificar cuenta y destinatarios; mostrar mensaje y adjunto y pedir aprobación. "
        "Revisar invoice_email_status. Para enviar, reservar con invoice_email_handoff reserve_send=true "
        "y registrar el resultado de Gmail. Si falta el conector, informar y conservar la propuesta.",
    }


def email_delivery_status(root: Path, draft_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", draft_id):
        raise ValueError("Identificador de factura inválido")
    path = root / "email-deliveries" / f"{draft_id}.json"
    if not path.exists():
        return {"draft_id": draft_id, "send_state": "not_reserved", "sent": False}
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("draft_id") != draft_id or state.get("send_state") not in (
        "sending",
        "uncertain",
        "sent",
    ):
        raise ValueError("Registro de envío inválido; revisar sin reenviar")
    return state


def reserve_email_send(root: Path, proposal: str, scope: dict, payload_hash: str) -> dict:
    draft_id = scope["draft_id"]
    email_delivery_status(root, draft_id)
    with FileLock(str(root / f"email-send-{draft_id}.lock"), timeout=30):
        previous = email_delivery_status(root, draft_id)
        if previous["send_state"] != "not_reserved":
            return {
                **previous,
                "ready_for_gmail": False,
                "duplicate_blocked": True,
                "notice": "Ya hay un envío registrado o pendiente para esta factura. Consultar Enviados; no reenviar.",
            }
        state = {
            "draft_id": draft_id,
            "proposal_id": proposal,
            "attempt_id": uuid4().hex,
            "send_state": "sending",
            "sent": False,
            "payload_sha256": payload_hash,
            "attachment_sha256": scope["attachment_sha256"],
            "provider_message_id": None,
            "created_at": datetime.now(UTC).isoformat(),
        }
        write_private(root / "email-deliveries" / f"{draft_id}.json", json.dumps(state).encode())
        return {**state, "ready_for_gmail": True, "duplicate_blocked": False}


def record_email_result(
    root: Path, proposal: str, attempt_id: str, outcome: str, message_id=None
) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", proposal) or not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("Identificador de intento inválido")
    if outcome not in ("sent", "uncertain"):
        raise ValueError("Resultado de correo inválido")
    if outcome == "sent" and (
        not isinstance(message_id, str)
        or len(message_id) > 256
        or header_value(message_id) != message_id
    ):
        raise ValueError("Un envío confirmado requiere el ID exacto devuelto por Gmail")
    if outcome == "uncertain" and message_id is not None:
        raise ValueError("Revisar en Gmail el ID antes de confirmar el envío")
    review = json.loads(
        (root / "email-proposals" / f"{proposal}.review.json").read_text(encoding="utf-8")
    )
    if digest(review["scope"]) != proposal:
        raise ValueError("La propuesta cambió; revisar el intento")
    draft_id = review["scope"]["draft_id"]
    email_delivery_status(root, draft_id)  # Validate identity before composing a lock path.
    with FileLock(str(root / f"email-send-{draft_id}.lock"), timeout=30):
        state = email_delivery_status(root, draft_id)
        if state.get("proposal_id") != proposal or state.get("attempt_id") != attempt_id:
            raise ValueError("El resultado no coincide con el intento reservado")
        if state["send_state"] == "sent":
            if outcome == "sent" and state["provider_message_id"] == message_id:
                return state
            raise ValueError("El envío ya fue confirmado; no reemplazar su resultado")
        state.update(
            send_state=outcome,
            sent=outcome == "sent",
            provider_message_id=message_id,
            updated_at=datetime.now(UTC).isoformat(),
        )
        write_private(root / "email-deliveries" / f"{draft_id}.json", json.dumps(state).encode())
        return state


def email_handoff(root: Path, proposal: str, confirmation: str, *, reserve_send=False) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", proposal):
        raise ValueError("Identificador de propuesta inválido")
    if confirmation != "ENVIAR CORREO " + proposal:
        raise ValueError("Confirmación específica de correo requerida")
    folder = root / "email-proposals"
    review = json.loads((folder / f"{proposal}.review.json").read_text())
    scope = review["scope"]
    if digest(scope) != proposal:
        raise ValueError("La propuesta cambió; preparar y revisar otra propuesta")
    if not scope.get("sender_account"):
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
    if scope.get("recipients") is not None:
        recipients = email_recipients(scope["recipients"])
        if recipient_header(recipients) != scope["to"]:
            raise ValueError("Los destinatarios no coinciden con la propuesta")
    if json.loads(payload.read_text(encoding="utf-8")) != email_payload(
        scope, attachment.read_bytes()
    ):
        raise ValueError("El mensaje o el adjunto no coincide con la propuesta")
    ready = {
        **scope,
        "proposal_id": proposal,
        "gmail_payload_path": str(payload),
        "attachment_path": str(attachment),
        "payload_sha256": review["payload_sha256"],
        "ready_for_review": True,
        "ready_for_gmail": False,
        "sent": False,
        "notice": "Revisión o borrador solamente. Para enviar, obtener aprobación posterior y reservar con reserve_send=true.",
    }
    if reserve_send:
        reservation = reserve_email_send(root, proposal, scope, review["payload_sha256"])
        if not reservation["ready_for_gmail"]:
            return reservation
        ready.update(reservation)
        ready["notice"] = (
            "Intento único reservado. Registrar el resultado de Gmail; ante incertidumbre consultar Enviados sin reenviar."
        )
    return ready
