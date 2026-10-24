"""Offline synthetic QR sample, or an explicitly supplied exported draft snapshot.

No profile, journal, credentials or network. All built-in data is fictitious.
This script does NOT call prepare/issue or persist an authorization.
Missing emission place stays explicitly pending; it is never inferred from an address.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import zxingcpp
from PIL import Image
from pypdf import PdfReader

from arca_codex.authorization import make_authorization
from arca_codex.models import Invoice, digest
from arca_codex.pdf import render_invoice


def simulated_authorization(summary):
    inv = Invoice.model_validate(summary["invoice"])
    reply = {
        "FeCabResp": {
            "Cuit": summary["issuer"]["cuit"],
            "PtoVta": summary["point_of_sale"],
            "CbteTipo": inv.voucher_type,
            "CantReg": 1,
            "Resultado": "A",
        },
        "FeDetResp": {
            "FECAEDetResponse": {
                "Concepto": 2,
                "DocTipo": 80,
                "DocNro": inv.recipient_cuit,
                "CbteDesde": summary["number"],
                "CbteHasta": summary["number"],
                "CbteFch": inv.invoice_date.strftime("%Y%m%d"),
                "Resultado": "A",
                "CAE": "12345678901234",
                "CAEFchVto": (inv.invoice_date + timedelta(days=10)).strftime("%Y%m%d"),
            }
        },
    }
    return {**make_authorization(summary, reply), "simulated": True}


def synthetic_snapshot():
    """Check-digit-valid identifiers for fixtures; no actual taxpayer or bank account."""
    invoice = Invoice(
        voucher_type=211,
        recipient_cuit="30712345671",
        recipient_name="CLIENTE FICTICIO SA",
        recipient_address="Domicilio ficticio 456",
        recipient_iva_id=1,
        recipient_iva_label="IVA Responsable Inscripto",
        total="123456.78",
        invoice_date="2026-10-16",
        service_from="2026-09-01",
        service_to="2026-09-30",
        due_date="2026-11-16",
        description="MUESTRA TÉCNICA - servicio ficticio",
        sale_condition="Transferencia bancaria",
        commercial_reference="DEMO-FCE-001",
        cbu="0000000000000000000000",
        transfer="SCA",
    )
    summary = {
        "environment": "homologacion",
        "point_of_sale": 2,
        "number": 8,
        "issuer": {
            "cuit": "20123456786",
            "name": "EMISOR FICTICIO",
            "address": "Domicilio ficticio 123",
            "gross_income_registration": "Exento",
            "activity_start": "2024-01-01",
        },
        "invoice": invoice.model_dump(mode="json"),
    }
    return {"draft_id": "synthetic-demo-not-persisted", "state": "prepared", "summary": summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "snapshot", type=Path, nargs="?", help="Copia local opcional; nunca publicar"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output/pdf"))
    parser.add_argument("--emission-place", help="Solo si fue confirmado por el usuario")
    args = parser.parse_args()
    original = args.snapshot.read_bytes() if args.snapshot else None
    snapshot = json.loads(original) if original is not None else synthetic_snapshot()
    emission_place = args.emission_place
    if not args.snapshot and not emission_place:
        emission_place = "Localidad ficticia de prueba"
    summary = snapshot["summary"]
    if summary["environment"] != "homologacion" or snapshot["state"] != "prepared":
        raise ValueError("Solo se admite una copia de un borrador preparado en homologación")
    identity = digest(summary)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    draft = args.output_dir / "demo-fce-borrador.pdf"
    sample = args.output_dir / "demo-fce-muestra-qr.pdf"
    render_invoice(summary, None, draft, emission_place=emission_place)
    result = simulated_authorization(summary)
    render_invoice(summary, result, sample, emission_place=emission_place, technical_sample=True)
    renderer = shutil.which("pdftoppm")
    if not renderer:
        raise RuntimeError("Instalar Poppler: se requiere renderizar antes de validar el QR")
    checks = {}
    for pdf in (draft, sample):
        reader = PdfReader(pdf)
        assert len(reader.pages) == 1, "Revisar visualmente si se amplía a varias páginas"
        prefix = args.output_dir / pdf.stem
        subprocess.run(
            [renderer, "-png", "-r", "180", "-singlefile", str(pdf), str(prefix)],
            check=True,
            capture_output=True,
        )
        with Image.open(prefix.with_suffix(".png")) as image:
            codes = zxingcpp.read_barcodes(image)
        text = reader.pages[0].extract_text()
        assert "SIN VALIDEZ FISCAL" in text
        assert f"Comprobante: {summary['number']:08d}" in text
        if pdf == draft:
            assert not codes and "CAE:" not in text
            checks["draft_without_cae_or_qr"] = True
            continue
        assert len(codes) == 1
        link = urlparse(codes[0].text)
        assert link.scheme == "https" and link.netloc == "www.arca.gob.ar"
        assert link.path == "/fe/qr/"
        data = json.loads(base64_decode(parse_qs(link.query)["p"][0]), parse_float=Decimal)
        expected = {
            "ver": 1,
            "fecha": summary["invoice"]["invoice_date"],
            "cuit": int(summary["issuer"]["cuit"]),
            "ptoVta": summary["point_of_sale"],
            "tipoCmp": summary["invoice"]["voucher_type"],
            "nroCmp": summary["number"],
            "importe": Decimal(summary["invoice"]["total"]),
            "moneda": "PES",
            "ctz": 1,
            "tipoDocRec": 80,
            "nroDocRec": int(summary["invoice"]["recipient_cuit"]),
            "tipoCodAut": "E",
            "codAut": int(result["cae"]),
        }
        assert data == expected
        assert isinstance(data["importe"], Decimal)
        assert all(
            type(data[k]) is int
            for k in (
                "ver",
                "cuit",
                "ptoVta",
                "tipoCmp",
                "nroCmp",
                "ctz",
                "tipoDocRec",
                "nroDocRec",
                "codAut",
            )
        )
        checks.update(
            qr_decoded_from_render=True, official_schema_matches=True, all_sample_fields_match=True
        )
    if args.snapshot:
        assert args.snapshot.read_bytes() == original
    assert digest(summary) == identity
    checks.update(
        snapshot_unchanged=True,
        draft_id=snapshot["draft_id"],
        summary_hash=identity,
        simulated_authorization=True,
        emission_place=emission_place,
        emission_place_pending=not bool(emission_place),
        synthetic_input=not bool(args.snapshot),
        checked_on=date.today().isoformat(),
    )
    report = args.output_dir / "verificacion-qr.json"
    report.write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print(sample.resolve())


def base64_decode(value):
    import base64

    return base64.b64decode(value, validate=True)


if __name__ == "__main__":
    main()
