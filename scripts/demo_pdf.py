"""Render only synthetic drafts and homologation documents. Never contact ARCA."""

from pathlib import Path

from pdf_sample import simulated_authorization

from arca_codex.models import Invoice
from arca_codex.pdf import render_invoice

data = Invoice(
    voucher_type=211,
    recipient_cuit="30712345671",
    recipient_name="CLIENTE DE PRUEBA SA",
    recipient_address="Domicilio ficticio 456",
    recipient_iva_id=1,
    recipient_iva_label="IVA Responsable Inscripto",
    total="123456.78",
    invoice_date="2026-10-24",
    service_from="2026-09-01",
    service_to="2026-09-30",
    due_date="2026-11-23",
    description="Asesoría integral comercial y de gestión - septiembre 2026",
    sale_condition="Transferencia bancaria",
    commercial_reference="DEMO-SEP-26",
    cbu="0000000000000000000000",
    transfer="SCA",
)
summary = {
    "environment": "homologacion",
    "issuer": {
        "cuit": "20123456786",
        "name": "EMISOR DE PRUEBA",
        "address": "Domicilio ficticio 123",
        "gross_income_registration": "Exento",
        "activity_start": "2024-01-01",
    },
    "point_of_sale": 2,
    "number": 8,
    "invoice": data.model_dump(mode="json"),
}
for name, result in [
    ("borrador", None),
    ("muestra-qr", simulated_authorization(summary)),
]:
    print(
        render_invoice(
            summary,
            result,
            Path("output/pdf") / f"demo-{name}.pdf",
            emission_place="Localidad ficticia de prueba",
            technical_sample=bool(result),
        )
    )
