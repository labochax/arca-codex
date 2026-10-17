"""Render only synthetic drafts and homologation documents. Never contact ARCA."""

from datetime import date
from pathlib import Path

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
    invoice_date=date.today(),
    service_from="2026-09-01",
    service_to="2026-09-30",
    due_date=date.today(),
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
    ("homologacion", {"cae": "12345678901234", "cae_due": date.today().isoformat()}),
]:
    print(render_invoice(summary, result, Path("output") / f"demo-{name}.pdf"))
