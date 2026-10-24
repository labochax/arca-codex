import base64
import copy
import json
import shutil
import subprocess
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest
import zxingcpp
from PIL import Image
from pypdf import PdfReader

from arca_codex import server
from arca_codex.authorization import make_authorization, validate_authorization
from arca_codex.engine import Engine
from arca_codex.models import Invoice, digest
from arca_codex.pdf import render_invoice
from arca_codex.representation import acceptance_days, amount_in_words, circulation_legend


@pytest.fixture
def summary(profile, invoice):
    return {
        "environment": "homologacion",
        "issuer": profile.issuer.model_dump(mode="json"),
        "point_of_sale": 2,
        "number": 8,
        "request_key": "pdf-test-001",
        "invoice": {**invoice, "total": "7654321.09"},
    }


def simulated_reply(summary):
    inv = Invoice.model_validate(summary["invoice"])
    return {
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
                "CAEFchVto": "20261103",
            }
        },
    }


@pytest.mark.parametrize(
    "value,words",
    [
        (
            "7654321.09",
            "SIETE MILLONES SEISCIENTOS CINCUENTA Y CUATRO MIL TRESCIENTOS VEINTIÚN PESOS CON 09/100",
        ),
        ("1.01", "UN PESO CON 01/100"),
        ("21.99", "VEINTIÚN PESOS CON 99/100"),
        ("31.25", "TREINTA Y UN PESOS CON 25/100"),
        ("100.00", "CIEN PESOS CON 00/100"),
        ("101.00", "CIENTO UN PESOS CON 00/100"),
        ("1000.00", "MIL PESOS CON 00/100"),
        ("21000.00", "VEINTIÚN MIL PESOS CON 00/100"),
        ("1000000.00", "UN MILLÓN DE PESOS CON 00/100"),
        ("1000000000.00", "MIL MILLONES DE PESOS CON 00/100"),
        ("1000000000000.00", "UN BILLÓN DE PESOS CON 00/100"),
    ],
)
def test_amount_in_words(value, words):
    assert amount_in_words(value) == words


def test_rule_dates_and_independent_circulation_legends():
    for d in ("2025-11-01", "2026-10-24", "2026-10-31"):
        assert acceptance_days(d) == 21
    for d in ("2025-10-31", "2026-11-01"):
        with pytest.raises(ValueError, match="vigencia"):
            acceptance_days(d)
    assert "Sistema de Circulación Abierta" in circulation_legend("SCA")
    assert "incluso en los Mercados" in circulation_legend("SCA")
    assert "Sistema de Circulación" not in circulation_legend("ADC")
    assert "para su negociación en los Mercados" in circulation_legend("ADC")


def render_png(pdf, tmp_path):
    executable = shutil.which("pdftoppm")
    assert executable, "Instalar Poppler para verificar el QR renderizado (no se omite la prueba)"
    prefix = tmp_path / pdf.stem
    subprocess.run(
        [executable, "-png", "-r", "180", "-singlefile", str(pdf), str(prefix)],
        check=True,
        capture_output=True,
    )
    return Image.open(prefix.with_suffix(".png"))


def test_technical_sample_qr_decodes_from_pdf_and_matches_every_field(summary, tmp_path):
    original = copy.deepcopy(summary)
    result = {**make_authorization(summary, simulated_reply(summary)), "simulated": True}
    pdf = tmp_path / "sample.pdf"
    render_invoice(
        summary, result, pdf, technical_sample=True, emission_place="Localidad de prueba"
    )
    reader = PdfReader(pdf)
    assert len(reader.pages) == 1
    text = reader.pages[0].extract_text()
    assert "SIN VALIDEZ FISCAL" in text and "NO OTORGADA POR ARCA" in text
    assert "CAE SIMULADO: 12345678901234" in text
    assert "Vencimiento CAE: 03-11-2026" in text
    assert "Comprobante: 00000008" in text and "Código Nº 211" in text
    assert "7.654.321,09" in text and "TRESCIENTOS VEINTIÚN PESOS CON 09/100" in text
    assert "21 días corridos" in text and "título ejecutivo" in text
    assert "Localidad de prueba" in text
    codes = zxingcpp.read_barcodes(render_png(pdf, tmp_path))
    assert len(codes) == 1
    url = urlparse(codes[0].text)
    assert url.scheme == "https" and url.netloc == "www.arca.gob.ar" and url.path == "/fe/qr/"
    raw = base64.b64decode(parse_qs(url.query)["p"][0], validate=True)
    data = json.loads(raw, parse_float=Decimal)
    assert data == {
        "ver": 1,
        "fecha": "2026-10-16",
        "cuit": 20123456786,
        "ptoVta": 2,
        "tipoCmp": 211,
        "nroCmp": 8,
        "importe": Decimal("7654321.09"),
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": 80,
        "nroDocRec": 30712345671,
        "tipoCodAut": "E",
        "codAut": 12345678901234,
    }
    assert isinstance(data["importe"], Decimal) and isinstance(data["codAut"], int)
    assert summary == original


def test_header_positions_and_draft_has_no_authorization_or_qr(summary, tmp_path):
    pdf = tmp_path / "draft.pdf"
    render_invoice(summary, None, pdf)
    reader = PdfReader(pdf)
    positions = []

    def visitor(text, cm, tm, font, size):
        if text.strip():
            positions.append((text.strip(), cm[4] + tm[4], cm[5] + tm[5], size))

    text = reader.pages[0].extract_text(visitor_text=visitor)
    letter = next(p for p in positions if p[0] == "C")
    assert abs(letter[1] + 12 - float(reader.pages[0].mediabox.width) / 2) < 3
    assert letter[3] >= 30
    name = next(p for p in positions if p[0] == "EMISOR DE PRUEBA")
    cuit = next(p for p in positions if "CUIT emisor" in p[0])
    assert name[1] < letter[1] < cuit[1]
    # All metadata in the right box share one left edge (the number used to be centered).
    metadata = [
        next(p for p in positions if label in p[0])
        for label in (
            "Punto de venta:",
            "Comprobante:",
            "Fecha de emisión:",
            "CUIT emisor:",
            "Ingresos Brutos:",
            "Inicio de actividades:",
        )
    ]
    assert max(p[1] for p in metadata) - min(p[1] for p in metadata) < 0.5
    assert all(a[2] > b[2] for a, b in zip(metadata, metadata[1:]))
    embedded = [
        font.get_object().get("/FontDescriptor")
        for font in reader.pages[0]["/Resources"]["/Font"].values()
    ]
    assert sum(bool(d and d.get_object().get("/FontFile2")) for d in embedded) >= 2
    assert "PENDIENTE DE CONFIRMACIÓN" in text
    assert "CAE:" not in text and "Comprobante autorizado" not in text
    assert not zxingcpp.read_barcodes(render_png(pdf, tmp_path))


def test_missing_place_or_unbound_result_cannot_export(summary, tmp_path):
    result = make_authorization(summary, simulated_reply(summary))
    with pytest.raises(ValueError, match="lugar de emisión"):
        render_invoice(summary, result, tmp_path / "missing.pdf")
    with pytest.raises(ValueError, match="vínculo"):
        render_invoice(
            summary,
            {"cae": result["cae"], "cae_due": result["cae_due"]},
            tmp_path / "legacy.pdf",
            emission_place="Localidad de prueba",
        )
    assert not (tmp_path / "missing.pdf").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("number", 9),
        ("point_of_sale", 3),
        ("environment", "produccion"),
        ("invoice.total", "1.00"),
        ("invoice.recipient_cuit", "20123456786"),
        ("invoice.invoice_date", "2026-10-15"),
        ("invoice.voucher_type", 11),
        ("invoice.description", "Detalle cambiado"),
        ("issuer.name", "OTRO EMISOR"),
    ],
)
def test_changed_summary_is_blocked(summary, field, value):
    result = make_authorization(summary, simulated_reply(summary))
    changed = copy.deepcopy(summary)
    keys = field.split(".")
    if len(keys) == 1:
        changed[keys[0]] = value
    else:
        changed[keys[0]][keys[1]] = value
    with pytest.raises(ValueError, match="vínculo"):
        validate_authorization(changed, result)


@pytest.mark.parametrize("field,value", [("cae", "99999999999999"), ("cae_due", "2026-12-01")])
def test_changed_authorization_is_blocked(summary, field, value):
    result = {**make_authorization(summary, simulated_reply(summary)), field: value}
    with pytest.raises(ValueError, match="respuesta persistida"):
        validate_authorization(summary, result)


@pytest.mark.parametrize(
    "field,value",
    [("DocNro", "20123456786"), ("CbteDesde", 9), ("CbteFch", "20261015"), ("ImpTotal", "1.00")],
)
def test_different_authorization_response_is_blocked(summary, field, value):
    reply = simulated_reply(summary)
    reply["FeDetResp"]["FECAEDetResponse"][field] = value
    with pytest.raises(ValueError, match="difiere"):
        make_authorization(summary, reply)


def test_simulated_authorization_never_renders_as_real_or_production(summary, tmp_path):
    result = {**make_authorization(summary, simulated_reply(summary)), "simulated": True}
    with pytest.raises(ValueError, match="simulada"):
        render_invoice(summary, result, tmp_path / "bad.pdf", emission_place="Prueba")
    production = {**summary, "environment": "produccion"}
    with pytest.raises(ValueError, match="solo en homologación"):
        render_invoice(production, result, tmp_path / "bad.pdf", technical_sample=True)


def test_production_fixture_has_cae_expiry_and_decodable_qr(summary, tmp_path):
    # In-memory fixture only: no production profile or ARCA service is used.
    summary = {**summary, "environment": "produccion"}
    result = make_authorization(summary, simulated_reply(summary))
    pdf = tmp_path / "production-unit-fixture.pdf"
    render_invoice(summary, result, pdf, emission_place="Localidad de prueba")
    text = PdfReader(pdf).pages[0].extract_text()
    assert "ORIGINAL" in text and "CAE: 12345678901234" in text
    assert "Vencimiento CAE: 03-11-2026" in text
    assert len(zxingcpp.read_barcodes(render_png(pdf, tmp_path))) == 1


def test_prepared_pdf_uses_persisted_number_and_preserves_journal(
    tmp_path,
    profile,
    invoice,
    arca,
    monkeypatch,
):
    e = Engine(profile, tmp_path, arca)
    draft = e.prepare(invoice, "persisted-pdf-001")
    before = (tmp_path / "journal.sqlite3").read_bytes()
    monkeypatch.setattr(server, "engine", lambda: e)
    out = server.preview_prepared_invoice(draft["draft_id"], "Localidad de prueba")
    text = PdfReader(out["pdf_path"]).pages[0].extract_text()
    assert "Comprobante: 00000008" in text and arca.calls == 0
    assert out["summary"] == draft["summary"]
    assert (tmp_path / "journal.sqlite3").read_bytes() == before
    assert digest(e.get(draft["draft_id"])["payload"]) == digest(draft["summary"])
    with pytest.raises(ValueError, match="Todavía no existe"):
        server.export_invoice_pdf(draft["draft_id"], "Localidad de prueba")


def test_future_fce_is_blocked_and_long_content_stays_legible(summary, tmp_path):
    future = copy.deepcopy(summary)
    future["invoice"].update(invoice_date="2026-11-01", due_date="2026-11-20")
    with pytest.raises(ValueError, match="vigencia"):
        render_invoice(future, None, tmp_path / "future.pdf")
    summary["invoice"]["description"] = ("Servicio de prueba con descripción extensa. " * 20)[:700]
    summary["invoice"]["recipient_name"] = "CLIENTE CON RAZÓN SOCIAL EXTENSA " * 3
    summary["issuer"]["address"] = ("Domicilio extenso de prueba " * 12)[:240]
    pdf = tmp_path / "long.pdf"
    render_invoice(summary, None, pdf, emission_place="Localidad de prueba")
    text = " ".join(page.extract_text() for page in PdfReader(pdf).pages)
    assert "Circulación" in text and "SIN VALIDEZ FISCAL" in text


@pytest.mark.parametrize(
    "field,value",
    [
        ("DocNro", "20123456786"),
        ("DocTipo", 99),
        ("Concepto", 1),
        ("CbteFch", "20261015"),
        ("CbteHasta", 9),
    ],
)
def test_engine_preserves_uncertain_on_response_mismatch(
    tmp_path,
    profile,
    invoice,
    arca,
    field,
    value,
):
    authorize = arca.authorize

    def mismatched(kind, detail):
        reply = authorize(kind, detail)
        reply["FeDetResp"]["FECAEDetResponse"][field] = value
        return reply

    arca.authorize = mismatched
    e = Engine(profile, tmp_path, arca)
    draft = e.prepare(invoice, "mismatch-pdf-001")
    out = e.emit(draft["draft_id"], draft["confirmation"], emission_place="Localidad de prueba")
    assert out["state"] == "uncertain" and arca.calls == 1
    with pytest.raises(ValueError, match="consultá su estado"):
        e.emit(draft["draft_id"], draft["confirmation"], emission_place="Localidad de prueba")
    assert arca.calls == 1


@pytest.mark.parametrize("reconcile", [False, True])
def test_export_uses_the_persisted_response_in_both_authorization_paths(
    tmp_path,
    profile,
    invoice,
    arca,
    monkeypatch,
    reconcile,
):
    e = Engine(profile, tmp_path, arca)
    draft = e.prepare(invoice, "binding-pdf-001")
    arca.fail = reconcile
    out = e.emit(draft["draft_id"], draft["confirmation"], emission_place="Localidad de prueba")
    if reconcile:
        assert out["state"] == "uncertain"
        out = e.reconcile(draft["draft_id"])
    assert out["state"] == "authorized"
    validate_authorization(out["summary"], out["result"])
    monkeypatch.setattr(server, "engine", lambda: e)
    pdf = server.export_invoice_pdf(draft["draft_id"], "Localidad de prueba")
    text = PdfReader(pdf["pdf_path"]).pages[0].extract_text()
    assert "Comprobante: 00000008" in text and "CAE: 12345678901234" in text
    assert "HOMOLOGACIÓN" in text and arca.calls == 1


def test_common_c_does_not_have_fce_fields_or_date_rule(summary, tmp_path):
    summary["invoice"].update(
        voucher_type=11,
        cbu=None,
        cbu_alias=None,
        transfer=None,
        invoice_date="2026-11-01",
        due_date="2026-11-20",
    )
    pdf = tmp_path / "common.pdf"
    render_invoice(summary, None, pdf)
    text = PdfReader(pdf).pages[0].extract_text()
    assert "Código Nº 011" in text
    assert "aceptada" not in text and "Lugar de emisión" not in text
    assert "CBU del emisor" not in text
