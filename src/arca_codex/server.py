from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from .engine import Engine
from .models import Invoice
from .pdf import render_invoice
from .soap import Wsfe
from .storage import data_home, load_profile

mcp = FastMCP(
    "ARCA Codex",
    instructions="Facturas C y FCE C de servicios en ARS. "
    "Preparar y mostrar datos antes de emitir. Nunca pedir claves, certificados o contraseñas por chat. "
    "Ante estado uncertain usar reconcile_invoice; nunca reintentar la emisión.",
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
    from .models import digest

    path = e.root / "output" / f"draft-{digest(summary)[:16]}.pdf"
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
    path = e.root / "output" / f"prepared-{draft_id[:16]}.pdf"
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
    path = e.root / "output" / f"invoice-{draft_id[:16]}.pdf"
    return {
        "pdf_path": render_invoice(
            row["payload"], row["result"], path, emission_place=emission_place
        ),
        "environment": e.profile.environment,
        "cae": row["result"]["cae"],
    }


def main():
    mcp.run(transport="stdio")
