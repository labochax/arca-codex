from __future__ import annotations

import base64
import io
import json
import re
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote
from xml.sax.saxutils import escape

from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .authorization import validate_authorization
from .models import Invoice
from .representation import acceptance_legend, amount_in_words, circulation_legend
from .storage import write_private


def amount(value):
    return f"{Decimal(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def qr_url(summary, result):
    inv = Invoice.model_validate(summary["invoice"]).model_dump(mode="json")
    if not 1 <= summary["number"] <= 99999999 or not 1 <= summary["point_of_sale"] <= 99999:
        raise ValueError("El QR requiere punto y número válidos")
    if not re.fullmatch(r"[1-9][0-9]{13}", str(result["cae"])):
        raise ValueError("El QR requiere un CAE de 14 dígitos")
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


def render_invoice(
    summary: dict,
    result: dict | None,
    path: Path,
    *,
    emission_place: str | None = None,
    technical_sample: bool = False,
):
    # Never mutate the persisted summary to add presentation-only metadata.
    Invoice.model_validate(summary["invoice"])
    issuer, inv = summary["issuer"], summary["invoice"]
    authorized = bool(result and result.get("cae"))
    production = summary["environment"] == "produccion"
    is_fce = inv["voucher_type"] == 211
    if summary["environment"] not in ("homologacion", "produccion"):
        raise ValueError("Ambiente desconocido")
    emission_place = emission_place.strip() if emission_place else None
    if emission_place and (len(emission_place) > 120 or any(ord(c) < 32 for c in emission_place)):
        raise ValueError("Lugar de emisión inválido")
    if technical_sample:
        if production or not authorized or result.get("simulated") is not True:
            raise ValueError("Muestra técnica solo en homologación con autorización simulada")
    elif result and result.get("simulated"):
        raise ValueError("Una autorización simulada no es un comprobante autorizado")
    if result and not authorized:
        raise ValueError("Resultado sin autorización")
    if authorized:
        validate_authorization(summary, result)
        if is_fce and not emission_place and not technical_sample:
            raise ValueError("Confirmar lugar de emisión para completar la FCE")
    legal_legend = acceptance_legend(inv["invoice_date"]) if is_fce else None
    styles = getSampleStyleSheet()
    styles["Normal"].fontSize = 8.5
    styles["Normal"].leading = 11
    styles["Heading2"].fontSize = 11
    styles["Heading2"].leading = 14
    styles.add(
        ParagraphStyle(
            "Letter", fontName="Helvetica-Bold", fontSize=34, leading=38, alignment=TA_CENTER
        )
    )
    styles.add(ParagraphStyle("VoucherCode", fontSize=8, leading=11, alignment=TA_CENTER))
    styles.add(ParagraphStyle("Small", fontSize=7.5, leading=10))

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
    title = "FACTURA DE CRÉDITO ELECTRÓNICA MiPyMEs" if is_fce else "FACTURA"
    label = (
        "MUESTRA TÉCNICA - SIN VALIDEZ FISCAL - CAE SIMULADO"
        if technical_sample
        else "ORIGINAL"
        if authorized and production
        else "HOMOLOGACIÓN - SIN VALIDEZ FISCAL"
        if authorized
        else "BORRADOR - SIN CAE - SIN VALIDEZ FISCAL"
    )
    story = [p(label, "Heading2")]
    if technical_sample:
        story.append(
            p("Autorización simulada para verificar el QR. No otorgada por ARCA.", "Small")
        )
    number = f"{summary['number']:08d}" if summary.get("number") else "SIN ASIGNAR"
    emisor = [
        p(issuer["name"], "Heading2"),
        p(issuer["address"]),
        p("RESPONSABLE MONOTRIBUTO"),
    ]
    document = [
        p(title, "Heading2"),
        p(f"Punto de venta: {summary['point_of_sale']:05d}"),
        p(f"Comprobante: {number}", "VoucherCode"),
        p(f"Fecha de emisión: {inv['invoice_date']}"),
        p(f"CUIT emisor: {issuer['cuit']}"),
        p(f"Ingresos Brutos: {issuer['gross_income_registration']}"),
        p(f"Inicio de actividades: {issuer['activity_start']}"),
    ]
    header = Table(
        [
            [
                emisor,
                [p("C", "Letter"), p(f"Código Nº {inv['voucher_type']:03d}", "VoucherCode")],
                document,
            ]
        ],
        colWidths=[72 * mm, 30 * mm, 72 * mm],
        minRowHeights=[40 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (0, 0), 0.6, colors.black),
                ("BOX", (2, 0), (2, 0), 0.6, colors.black),
                ("BOX", (1, 0), (1, 0), 0.6, colors.black),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([header, Spacer(1, 3 * mm)])
    if is_fce:
        place = emission_place or "PENDIENTE DE CONFIRMACIÓN (borrador incompleto)"
        story.append(p(f"Lugar de emisión: {place}"))
    receptor = [
        p(inv["recipient_name"], "Heading2"),
        p(inv["recipient_address"]),
        p(f"CUIT: {inv['recipient_cuit']}"),
        p(inv["recipient_iva_label"]),
        p(f"Condición de venta: {inv['sale_condition']}"),
    ]
    info = Table([[receptor]], colWidths=[174 * mm])
    info.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#bac6cc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story += [
        info,
        Spacer(1, 3 * mm),
        p(f"Período facturado: {inv['service_from']} al {inv['service_to']}"),
        p(f"Vencimiento para el pago: {inv['due_date']}"),
        Spacer(1, 3 * mm),
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
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story += [
        lines,
        Spacer(1, 2 * mm),
        p(f"Subtotal: $ {amount(inv['total'])}"),
        p("Otros tributos: $ 0,00"),
        p(f"TOTAL ARS $ {amount(inv['total'])}", "Heading2"),
    ]
    if is_fce:
        story.append(p("Son: " + amount_in_words(inv["total"])))
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
            Spacer(1, 2 * mm),
            p("Aceptación y carácter ejecutivo - Ley 27.440, art. 5", "Small"),
            p(legal_legend, "Small"),
            p("Plazo: Resolución 219/2025, vigente del 01/11/2025 al 31/10/2026.", "Small"),
            Spacer(1, 2 * mm),
            p("Circulación - Resolución 103/2020, art. 3, inc. b", "Small"),
            p(circulation_legend(inv["transfer"]), "Small"),
        ]
    story.append(Spacer(1, 3 * mm))
    if authorized:
        footer = [
            p(
                "AUTORIZACIÓN SIMULADA - NO OTORGADA POR ARCA"
                if technical_sample
                else "Comprobante autorizado"
                if production
                else "Autorización de homologación"
            ),
            p(("CAE SIMULADO: " if technical_sample else "CAE: ") + result["cae"]),
            p("Vencimiento CAE: " + result["cae_due"]),
            p("ARCA no se responsabiliza por los datos ingresados en el detalle de la operación."),
        ]
        if production or technical_sample:
            qr = QrCodeWidget(qr_url(summary, result))
            x1, y1, x2, y2 = qr.getBounds()
            size = 42 * mm
            drawing = Drawing(
                size, size, transform=[size / (x2 - x1), 0, 0, size / (y2 - y1), 0, 0]
            )
            drawing.add(qr)
            story.append(KeepTogether(Table([[drawing, footer]], colWidths=[48 * mm, 126 * mm])))
        else:
            story.extend(footer)
    else:
        story.append(
            p("Este documento no acredita una emisión ni un pago. Falta autorización de ARCA.")
        )

    def page_marks(canvas, document):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.drawString(18 * mm, 9 * mm, label)
        canvas.drawRightString(192 * mm, 9 * mm, f"Página {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=page_marks, onLaterPages=page_marks)
    write_private(path, buffer.getvalue())
    return str(path)
