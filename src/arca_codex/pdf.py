from __future__ import annotations

import base64
import io
import json
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .storage import write_private


def amount(value):
    return f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def qr_url(summary, result):
    inv = summary["invoice"]
    # Monetary value must be a JSON number in ARCA's QR schema. Serialize Decimal exactly.
    data = {
        "ver": 1,
        "fecha": inv["invoice_date"],
        "cuit": int(summary["issuer"]["cuit"]),
        "ptoVta": summary["point_of_sale"],
        "tipoCmp": inv["voucher_type"],
        "nroCmp": summary["number"],
        "importe": "__AMOUNT__",
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": 80,
        "nroDocRec": int(inv["recipient_cuit"]),
        "tipoCodAut": "E",
        "codAut": int(result["cae"]),
    }
    raw = json.dumps(data, separators=(",", ":")).replace('"__AMOUNT__"', inv["total"])
    return "https://www.arca.gob.ar/fe/qr/?p=" + quote(
        base64.b64encode(raw.encode()).decode(), safe=""
    )


def render_invoice(summary: dict, result: dict | None, path: Path):
    issuer, inv = summary["issuer"], summary["invoice"]
    authorized = bool(result and result.get("cae"))
    production = summary["environment"] == "produccion"
    is_fce = inv["voucher_type"] == 211
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 9
    styles["Normal"].leading = 13
    styles["Heading1"].fontSize = 19
    styles["Heading1"].leading = 24

    def p(text, style="Normal"):
        return Paragraph(escape(str(text)).replace("\n", "<br/>"), styles[style])

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Factura electrónica",
        author=issuer["name"],
    )
    title = "FACTURA DE CRÉDITO ELECTRÓNICA MiPyMEs C" if is_fce else "FACTURA C"
    label = (
        "ORIGINAL"
        if authorized and production
        else "HOMOLOGACIÓN — SIN VALIDEZ FISCAL"
        if authorized
        else "BORRADOR — SIN CAE — SIN VALIDEZ FISCAL"
    )
    story = [
        p(label, "Heading2"),
        p(title, "Heading1"),
        p(
            f"Código {inv['voucher_type']:03d}   •   Punto de venta {summary['point_of_sale']:05d}   •   "
            f"Comprobante {summary.get('number', 0):08d}"
        ),
        p(f"Fecha de emisión: {inv['invoice_date']}"),
        Spacer(1, 7 * mm),
    ]
    emisor = [
        p(issuer["name"], "Heading2"),
        p(issuer["address"]),
        p(f"CUIT: {issuer['cuit']}"),
        p("IVA: Responsable Monotributo"),
        p(f"Ingresos Brutos: {issuer['gross_income_registration']}"),
        p(f"Inicio de actividades: {issuer['activity_start']}"),
    ]
    receptor = [
        p(inv["recipient_name"], "Heading2"),
        p(inv["recipient_address"]),
        p(f"CUIT: {inv['recipient_cuit']}"),
        p(inv["recipient_iva_label"]),
        p(f"Condición de venta: {inv['sale_condition']}"),
    ]
    info = Table([[emisor, receptor]], colWidths=[87 * mm, 87 * mm])
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bac6cc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ]
        )
    )
    story += [
        info,
        Spacer(1, 6 * mm),
        p(f"Período facturado: {inv['service_from']} al {inv['service_to']}"),
        p(f"Vencimiento para el pago: {inv['due_date']}"),
        Spacer(1, 6 * mm),
    ]
    lines = Table(
        [
            [p("Descripción"), p("Cantidad"), p("Precio unitario"), p("Subtotal")],
            [
                p(inv["description"]),
                p("1 unidad"),
                p("$ " + amount(inv["total"])),
                p("$ " + amount(inv["total"])),
            ],
        ],
        colWidths=[84 * mm, 23 * mm, 34 * mm, 33 * mm],
        repeatRows=1,
    )
    lines.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e7eef2")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bac6cc")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
            ]
        )
    )
    story += [
        lines,
        Spacer(1, 5 * mm),
        p(f"Subtotal: $ {amount(inv['total'])}"),
        p("Otros tributos: $ 0,00"),
        p(f"TOTAL ARS $ {amount(inv['total'])}", "Heading2"),
    ]
    if inv.get("commercial_reference"):
        story.append(p("Referencia comercial: " + inv["commercial_reference"]))
    if is_fce:
        story += [p("CBU del emisor: " + inv["cbu"])]
        if inv.get("cbu_alias"):
            story.append(p("Alias CBU: " + inv["cbu_alias"]))
        transfer = (
            "Sistema de Circulación Abierta"
            if inv["transfer"] == "SCA"
            else "Agente de Depósito Colectivo"
        )
        story += [
            p("Opción de transferencia: " + transfer),
            Spacer(1, 5 * mm),
            p(
                "Luego de su aceptación tácita o expresa, esta Factura de Crédito Electrónica "
                "MiPyMEs será transmitida a " + transfer + " para su circulación y negociación, "
                "incluso en los Mercados de Valores, a través de un Agente de Depósito Colectivo "
                "o agentes que cumplan similares funciones."
            ),
        ]
    story.append(Spacer(1, 8 * mm))
    if authorized:
        footer = [
            p("Comprobante autorizado" if production else "Autorización de prueba"),
            p("CAE: " + result["cae"]),
            p("Vencimiento CAE: " + result["cae_due"]),
            p("ARCA no se responsabiliza por los datos ingresados en el detalle de la operación."),
        ]
        if production:
            qr = QrCodeWidget(qr_url(summary, result))
            x1, y1, x2, y2 = qr.getBounds()
            size = 32 * mm
            drawing = Drawing(
                size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0]
            )
            drawing.add(qr)
            story.append(KeepTogether(Table([[drawing, footer]], colWidths=[40 * mm, 134 * mm])))
        else:
            story.extend(footer)
    else:
        story.append(
            p("Este documento no acredita una emisión ni un pago. Falta autorización de ARCA.")
        )
    doc.build(story)
    write_private(path, buffer.getvalue())
    return str(path)
