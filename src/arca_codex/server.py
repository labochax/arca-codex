from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from . import delivery
from .authorization import validate_authorization
from .engine import Engine
from .models import Invoice
from .pdf import render_invoice
from .soap import Wsfe
from .storage import data_home, load_profile

mcp = FastMCP(
    "ARCA Codex",
    instructions="Facturas C y FCE C de servicios en ARS. "
    "Preparar y mostrar datos antes de emitir. Nunca pedir claves, certificados o contraseñas por chat. "
    "Ante estado uncertain usar reconcile_invoice; nunca reintentar la emisión. "
    "Antes de archivar preguntar fecha del nombre y carpeta con invoice_output_options. "
    "Después de guardar ofrecer Gmail, preguntar contacto y correo y mostrar el mensaje "
    "y la cuenta remitente antes de pedir autorización de envío. Reservar un solo intento "
    "con invoice_email_handoff reserve_send=true y registrar el ID de Gmail o la incertidumbre. "
    "Este MCP no envía correo.",
)
READ = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
)
LOCAL = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
WRITE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True
)


def engine():
    root = data_home()
    profile = load_profile(root)
    return Engine(profile, root, Wsfe(profile, root))


@mcp.tool(annotations=READ)
def configuration_status() -> dict[str, Any]:
    """Reportar configuración sin exponer secretos; no emitir ni autenticar con ARCA."""
    root = data_home()
    if not (root / "profile.json").exists():
        return {"configured": False, "next_step": "Ejecutar arca-codex setup en una terminal local"}
    p = load_profile(root)
    return {
        "configured": True,
        "environment": p.environment,
        "issuer_cuit": p.issuer.cuit,
        "point_of_sale": p.point_of_sale,
        "production_enabled": p.production_enabled,
    }


@mcp.tool(annotations=READ)
def arca_health() -> dict[str, Any]:
    """Consultar FEDummy en el ambiente configurado, sin enviar credenciales."""
    return engine().client.health()


@mcp.tool(annotations=READ)
def billing_parameters() -> dict[str, Any]:
    """Consultar puntos de venta WSFE y condiciones IVA válidas para clase C."""
    e = engine()
    points = e.client.points()
    return {
        "points_of_sale": points,
        "points_catalog_available": bool(points),
        "notice": (
            "Homologación sin catálogo de puntos: preparar consultará el último número. "
            "La habilitación para emitir sigue sujeta a la autorización de ARCA."
            if not points and e.profile.environment == "homologacion"
            else None
        ),
        "recipient_iva_conditions": e.client.iva_conditions(),
    }


@mcp.tool(annotations=LOCAL)
def preview_invoice(invoice: Invoice, emission_place: str | None = None) -> dict[str, Any]:
    """Validar una factura y generar un PDF BORRADOR sin conectarse a ARCA. Ver esquema en skill."""
    e = engine()
    summary = e.preview(invoice)
    path = e.root / "previews" / delivery.pdf_filename(summary, draft=True)
    return {
        "summary": summary,
        "pdf_path": render_invoice(summary, None, path, emission_place=emission_place),
    }


@mcp.tool(annotations=LOCAL)
def preview_prepared_invoice(draft_id: str, emission_place: str | None = None) -> dict[str, Any]:
    """PDF BORRADOR desde el resumen persistido y su número exacto, sin emitir ni consultar ARCA.

    emission_place es un dato gráfico confirmado por el usuario; no modifica el borrador.
    """
    e = engine()
    row = e.get(draft_id)
    path = e.root / "previews" / delivery.pdf_filename(row["payload"], draft=True)
    return {
        "summary": row["payload"],
        "state": row["state"],
        "pdf_path": render_invoice(row["payload"], None, path, emission_place=emission_place),
    }


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )
)
def prepare_invoice(invoice: Invoice, request_key: str) -> dict[str, Any]:
    """Preparar borrador persistente con número consultado en ARCA. No emitir.
    Mostrar summary y pedir aprobación del usuario. Reutilizar request_key en reintentos."""
    return engine().prepare(invoice, request_key)


@mcp.tool(annotations=WRITE)
def issue_invoice(
    draft_id: str, confirmation: str, emission_place: str | None = None
) -> dict[str, Any]:
    """Emitir comprobante con efectos fiscales (FECAESolicitar). Requiere aprobación explícita
    del usuario sobre el resumen exacto y confirmation retornado por prepare_invoice.
    Para FCE requiere emission_place confirmado en el resumen mostrado al usuario.
    El hash vincula los datos; no prueba consentimiento humano. Ante uncertain conciliar."""
    return engine().emit(draft_id, confirmation, emission_place=emission_place)


@mcp.tool(annotations=READ)
def invoice_status(draft_id: str) -> dict[str, Any]:
    """Leer el estado persistido del borrador; no consultar ni modificar ARCA."""
    e = engine()
    return e.public(e.get(draft_id))


@mcp.tool(annotations=READ)
def consult_invoice(
    environment: Literal["homologacion", "produccion"],
    issuer_cuit: str,
    point_of_sale: Annotated[int, Field(strict=True, ge=1, le=99999)],
    voucher_type: Literal[11, 211],
    number: Annotated[int, Field(strict=True, ge=1, le=99999999)],
) -> dict[str, Any]:
    """Consultar siempre ARCA mediante FECompConsultar, incluso si ya está autorizado localmente.

    Solo lectura: no consulta ni modifica el diario, no concilia ni solicita CAE.
    Ambiente, CUIT y punto deben coincidir con el perfil; no cambia la configuración.
    Devuelve únicamente campos fiscales permitidos, sin XML, credenciales ni tickets.
    """
    e = engine()
    if environment != e.profile.environment:
        raise ValueError("El ambiente solicitado no coincide con el perfil configurado")
    if issuer_cuit != e.profile.issuer.cuit:
        raise ValueError("El CUIT solicitado no coincide con el emisor configurado")
    if point_of_sale != e.profile.point_of_sale:
        raise ValueError("El punto de venta solicitado no coincide con el perfil configurado")
    remote = e.client.consult(voucher_type, number)
    fields = (
        "Concepto",
        "DocTipo",
        "DocNro",
        "CbteDesde",
        "CbteHasta",
        "CbteFch",
        "ImpTotal",
        "ImpTotConc",
        "ImpNeto",
        "ImpOpEx",
        "ImpTrib",
        "ImpIVA",
        "MonId",
        "MonCotiz",
        "FchServDesde",
        "FchServHasta",
        "FchVtoPago",
        "CondicionIVAReceptorId",
        "PtoVta",
        "CbteTipo",
        "Resultado",
        "EmisionTipo",
        "CodAutorizacion",
        "FchVto",
    )
    # Do not return arbitrary response fields or nested structures from a remote service.
    fiscal = (
        None
        if remote is None
        else {
            key: remote[key]
            for key in fields
            if key in remote and isinstance(remote[key], (str, int, float))
        }
    )
    return {
        "source": "ARCA FECompConsultar",
        "queried_at": datetime.now(UTC).isoformat(),
        "environment": e.profile.environment,
        "issuer_cuit": e.profile.issuer.cuit,
        "point_of_sale": e.profile.point_of_sale,
        "voucher_type": voucher_type,
        "number": number,
        "found": remote is not None,
        "voucher": fiscal,
    }


@mcp.tool(
    annotations=ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )
)
def reconcile_invoice(draft_id: str) -> dict[str, Any]:
    """Consultar un envío incierto en ARCA y conciliar el diario local; nunca volver a emitir."""
    return engine().reconcile(draft_id)


@mcp.tool(annotations=LOCAL)
def export_invoice_pdf(draft_id: str, emission_place: str | None = None) -> dict[str, Any]:
    """Generar PDF con QR solo desde una autorización persistida; homologación lleva marca de prueba."""
    e = engine()
    row = e.get(draft_id)
    if row["state"] != "authorized":
        raise ValueError("Todavía no existe una autorización para generar el comprobante final")
    # Re-rendering must not overwrite an archived or already approved attachment.
    path = e.root / "rendered" / draft_id / delivery.pdf_filename(row["payload"])
    return {
        "pdf_path": render_invoice(
            row["payload"], row["result"], path, emission_place=emission_place
        ),
        "environment": e.profile.environment,
        "cae": row["result"]["cae"],
        "requires_output_confirmation": True,
        "next_step": "Usar invoice_output_options y preguntar fecha de archivo y carpeta. "
        "La copia privada no acredita guardado en la carpeta elegida ni sincronización con Drive.",
    }


@mcp.tool(annotations=READ)
def invoice_output_options(
    draft_id: str, filing_period: str | None = None, output_directory: str | None = None
) -> dict[str, Any]:
    """Proponer nombre por período del servicio y carpeta privada recordada, sin guardar allí.

    Preguntar si el período/fecha del nombre y carpeta están bien o se deben cambiar.
    filing_period admite AAAA-MM, AAAA-MM-DD o AAAA-MM-DD_al_AAAA-MM-DD.
    Solo afecta el nombre, nunca las fechas fiscales. Mostrar la ruta antes de confirmar.
    """
    e = engine()
    return delivery.output_options(
        e.root, e.get(draft_id), period=filing_period, directory=output_directory
    )


@mcp.tool(annotations=LOCAL)
def save_invoice_pdf(
    draft_id: str,
    confirmation: str,
    filing_period: str | None = None,
    output_directory: str | None = None,
    remember_directory: bool = False,
    emission_place: str | None = None,
) -> dict[str, Any]:
    """Guardar factura ya autorizada en la carpeta y con la fecha de archivo aprobadas.

    Requiere confirmation de invoice_output_options para esos mismos parámetros.
    remember_directory recuerda la carpeta solo en este perfil local. No sobrescribir
    archivos distintos, emitir, cambiar el diario ni enviar emails. En Drive para
    escritorio comprueba guardado local, no que se haya completado la sincronización.
    """
    e = engine()
    row = e.get(draft_id)
    if row["state"] != "authorized":
        raise ValueError("Solo se puede archivar un comprobante autorizado")
    options = delivery.output_options(e.root, row, period=filing_period, directory=output_directory)
    if confirmation != options["confirmation"]:
        raise ValueError("Confirmá la fecha de archivo y la carpeta propuestas antes de guardar")
    validate_authorization(row["payload"], row["result"])
    # Repeat saves use the exact saved bytes rather than re-rendering creation timestamps.
    source = delivery.saved_attachment(e.root, row)
    if source is None:
        from pathlib import Path

        source = Path(export_invoice_pdf(draft_id, emission_place)["pdf_path"])
    return delivery.save_pdf(
        e.root,
        row,
        source,
        confirmation,
        period=filing_period,
        directory=output_directory,
        remember_directory=remember_directory,
    )


@mcp.tool(annotations=LOCAL)
def prepare_invoice_email(
    draft_id: str,
    recipient_name: str | None = None,
    recipient_email: str | None = None,
    emission_place: str | None = None,
    sender_email: str | None = None,
    recipients: list[delivery.EmailRecipient] | None = None,
    subject: str | None = None,
    body: str | None = None,
) -> dict[str, Any]:
    """Preparar asunto, cuerpo y PDF para Gmail. No crear borradores remotos ni enviar.

    recipients admite varios contactos con name/email, todos en Para. No combinarlo
    con recipient_name/recipient_email. subject/body conservan el texto aprobado.
    Si faltan contacto o email, pedirlos al usuario. Verificar la cuenta Gmail remitente,
    mostrar destinatario/asunto/cuerpo/adjunto y pedir aprobación antes de usar herramientas
    Gmail disponibles en el host. La autorización fiscal NO autoriza el envío por correo.
    No almacenar destinatarios predeterminados ni exponer el PDF en base64 en la conversación.
    """
    e = engine()
    row = e.get(draft_id)
    if row["state"] != "authorized" or e.profile.environment != "produccion":
        raise ValueError("El correo al cliente requiere una factura de producción autorizada")
    validate_authorization(row["payload"], row["result"])
    missing = [
        field
        for field, value in (
            ("recipient_name", recipient_name),
            ("recipient_email", recipient_email),
        )
        if not value
    ]
    if missing and recipients is None:
        return {
            "sent": False,
            "requires_recipient_confirmation": True,
            "missing": missing,
            "question": "¿A qué contacto y correo querés enviar la factura?",
        }
    attachment = delivery.saved_attachment(e.root, row)
    if attachment is None:
        from pathlib import Path

        attachment = Path(export_invoice_pdf(draft_id, emission_place)["pdf_path"])
    return delivery.prepare_email(
        e.root,
        row,
        attachment,
        recipient_name,
        recipient_email,
        sender_email,
        recipients=recipients,
        subject=subject,
        body=body,
    )


@mcp.tool(annotations=LOCAL)
def invoice_email_handoff(
    proposal_id: str, confirmation: str, reserve_send: bool = False
) -> dict[str, Any]:
    """Validar propuesta de correo y PDF después de la aprobación para entregarlos a Gmail.

    NO envía ni crea borradores remotos. Requiere cuenta remitente identificada y
    confirmation exacta de prepare_invoice_email. El hash no acredita consentimiento
    humano; el host debe haber obtenido autorización para ese mensaje y adjunto.
    Por defecto valida solo revisión/borrador. Tras aprobar el envío, usar
    reserve_send=true: reserva un único intento persistente por factura. Solo llamar
    a Gmail si ready_for_gmail=true. Registrar el resultado; nunca repetir un intento
    sending/uncertain/sent, tampoco creando otra propuesta de esa factura.
    """
    return delivery.email_handoff(data_home(), proposal_id, confirmation, reserve_send=reserve_send)


@mcp.tool(annotations=READ)
def invoice_email_status(draft_id: str) -> dict[str, Any]:
    """Leer el registro local de entrega de esta factura; no consultar ni enviar Gmail.

    not_reserved no prueba ausencia de envíos anteriores/manuales: consultar el
    historial y Enviados antes de reservar. No equivale al estado fiscal.
    """
    e = engine()
    e.get(draft_id)
    return delivery.email_delivery_status(e.root, draft_id)


@mcp.tool(annotations=LOCAL)
def record_invoice_email_result(
    proposal_id: str,
    attempt_id: str,
    outcome: Literal["sent", "uncertain"],
    message_id: str | None = None,
) -> dict[str, Any]:
    """Registrar el resultado del intento reservado; no enviar ni consultar Gmail.

    sent requiere el ID exacto confirmado por el proveedor; timeout/resultado ambiguo
    requiere uncertain. Resolver mediante Enviados, sin reintentos ni desbloqueos.
    No reemplazar un ID confirmado ni atribuir a Gmail una confirmación inventada.
    """
    return delivery.record_email_result(data_home(), proposal_id, attempt_id, outcome, message_id)


def main():
    mcp.run(transport="stdio")
