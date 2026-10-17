import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest
from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring

from arca_codex.models import Invoice
from arca_codex.pdf import qr_url, render_invoice
from arca_codex.soap import FE, ArcaError, Wsfe, envelope, local, unpack


def test_request_serializes_fce_fields_in_schema_order(invoice):
    detail = Invoice.model_validate(invoice).detail(8)
    xml = envelope(
        "FECAESolicitar",
        {
            "FeCAEReq": {
                "FeCabReq": {"CantReg": 1, "PtoVta": 2, "CbteTipo": 211},
                "FeDetReq": {"FECAEDetRequest": detail},
            }
        },
    )
    root = fromstring(xml)
    d = root.find(f".//{{{FE}}}FECAEDetRequest")
    assert [local(n.tag) for n in d] == [
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
        "FchServDesde",
        "FchServHasta",
        "FchVtoPago",
        "MonId",
        "MonCotiz",
        "CondicionIVAReceptorId",
        "Opcionales",
    ]
    assert b"123456.78" in xml


def test_xml_escape_and_entity_rejection():
    root = fromstring(envelope("Demo", {"Value": "A < B & C"}))
    assert root.find(f".//{{{FE}}}Value").text == "A < B & C"
    with pytest.raises(DefusedXmlException):
        fromstring('<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>')


def test_realistic_consult_xml_is_parsed_without_losing_lists():
    root = fromstring("""<ResultGet xmlns="http://ar.gov.afip.dif.FEV1/">
        <ImpTotal>123456.78</ImpTotal><Opcionales><Opcional><Id>2101</Id><Valor>0000000000000000000000</Valor></Opcional>
        <Opcional><Id>27</Id><Valor>SCA</Valor></Opcional></Opcionales>
        <Resultado>A</Resultado><CodAutorizacion>12345678901234</CodAutorizacion><EmisionTipo>CAE</EmisionTipo>
        <FchVto>20261016</FchVto></ResultGet>""")
    r = unpack(root)
    assert len(r["Opcionales"]["Opcional"]) == 2
    assert r["CodAutorizacion"] == "12345678901234"


def test_consult_only_treats_602_as_missing(tmp_path, profile, monkeypatch):
    ws = Wsfe(profile, tmp_path)

    def missing(*args, **kwargs):
        raise ArcaError("missing", ["602"])

    monkeypatch.setattr(ws, "call", missing)
    assert ws.consult(211, 1) is None

    def auth(*args, **kwargs):
        raise ArcaError("auth", ["600"])

    monkeypatch.setattr(ws, "call", auth)
    with pytest.raises(ArcaError):
        ws.consult(211, 1)


def test_qr_exact_payload_and_pdf_draft(tmp_path, profile, invoice):
    summary = {
        "environment": "produccion",
        "issuer": profile.issuer.model_dump(mode="json"),
        "point_of_sale": 2,
        "number": 8,
        "invoice": invoice,
    }
    result = {"cae": "12345678901234", "cae_due": "2026-10-16"}
    link = qr_url(summary, result)
    raw = base64.b64decode(parse_qs(urlparse(link).query)["p"][0])
    data = json.loads(raw)
    assert data["tipoCmp"] == 211 and data["tipoCodAut"] == "E"
    assert data["importe"] == 123456.78 and data["nroCmp"] == 8
    assert b'"importe":123456.78' in raw
    dest = tmp_path / "test.pdf"
    render_invoice(summary, None, dest)
    assert dest.read_bytes().startswith(b"%PDF-")
