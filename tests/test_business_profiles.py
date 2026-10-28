import json

import pytest
from pydantic import ValidationError
from pypdf import PdfReader

from arca_codex.authorization import _equal
from arca_codex.credentials import credential_account
from arca_codex.engine import Engine
from arca_codex.models import Invoice, Profile
from arca_codex.pdf import qr_url, render_invoice
from arca_codex.storage import data_home


def business_profile(profile, regime="responsable_inscripto"):
    result = profile.model_copy(deep=True)
    result.issuer.tax_regime = regime
    result.ordinary_a_authorized = True
    return result


def business_invoice(invoice, kind=1):
    data = {k: v for k, v in invoice.items() if k not in ("cbu", "transfer", "cbu_alias")}
    return {
        **data,
        "voucher_type": kind,
        "net_amount": "1000.00",
        "vat_id": 5,
        "vat_amount": "210.00",
        "total": "1210.00",
        "recipient_iva_id": 4 if kind == 6 else 1,
        "recipient_iva_label": "IVA Sujeto Exento" if kind == 6 else "IVA Responsable Inscripto",
    }


@pytest.mark.parametrize("kind", [1, 6])
def test_business_soap_and_rendering(tmp_path, profile, invoice, kind):
    p = business_profile(profile)
    inv = Invoice(**business_invoice(invoice, kind))
    detail = inv.detail(8)
    assert detail["ImpNeto"] == "1000.00"
    assert detail["ImpIVA"] == "210.00"
    assert detail["Iva"]["AlicIva"] == [{"Id": 5, "BaseImp": "1000.00", "Importe": "210.00"}]
    e = Engine(p, tmp_path, None)
    summary = e.preview(inv)
    path = tmp_path / "business.pdf"
    render_invoice(summary, None, path)
    text = "\n".join(x.extract_text() for x in PdfReader(path).pages)
    assert "IVA RESPONSABLE INSCRIPTO" in text
    assert "Neto gravado: $ 1.000,00" in text
    assert "IVA (21%): $ 210,00" in text
    assert "TOTAL ARS $ 1.210,00" in text
    assert len(PdfReader(path).pages) == 1
    if kind == 6:
        assert "IVA contenido: $ 210,00" in text
    summary["number"] = 8
    assert "https://www.arca.gob.ar/fe/qr/" in qr_url(summary, {"cae": "12345678901234"})


@pytest.mark.parametrize(
    "changes",
    [
        {"total": "1200"},
        {"vat_amount": "211"},
        {"net_amount": None},
        {"vat_id": 99},
        {"recipient_iva_id": 5},
    ],
)
def test_business_rejects_inconsistent_amounts(invoice, changes):
    with pytest.raises(ValidationError):
        Invoice(**{**business_invoice(invoice), **changes})


def test_regime_cannot_be_changed_by_invoice(tmp_path, profile, invoice):
    with pytest.raises(ValueError, match="Monotributista"):
        Engine(profile, tmp_path, None).preview(business_invoice(invoice))
    p = business_profile(profile)
    with pytest.raises(ValueError, match="Responsable inscripto"):
        Engine(p, tmp_path, None).preview(invoice)
    p.ordinary_a_authorized = False
    with pytest.raises(ValueError, match="A común"):
        Engine(p, tmp_path, None).preview(business_invoice(invoice))


def test_exempt_issuer_c_and_old_hashes(tmp_path, profile, invoice):
    p = business_profile(profile, "exento")
    simple = {k: v for k, v in invoice.items() if k not in ("cbu", "transfer")}
    simple["voucher_type"] = 11
    summary = Engine(p, tmp_path, None).preview(simple)
    assert summary["issuer"]["tax_regime"] == "exento"
    legacy_issuer = profile.issuer.model_dump(mode="json")
    assert "tax_regime" not in legacy_issuer
    legacy = Invoice(**invoice).model_dump(mode="json")
    assert not set(legacy) & {"net_amount", "vat_amount", "vat_id"}
    with pytest.raises(ValidationError, match="no discrimina IVA"):
        Invoice(**{**invoice, "vat_amount": "0"})


def test_vat_comparison_is_numeric_and_order_independent():
    a = {"AlicIva": [{"Id": "5", "BaseImp": "1000", "Importe": "210"}]}
    b = {"AlicIva": [{"Id": 5, "BaseImp": "1000.00", "Importe": "210.00"}]}
    assert _equal("Iva", a, b)
    b["AlicIva"][0]["Importe"] = "211.00"
    assert not _equal("Iva", a, b)


def test_profiles_keep_roots_and_credentials_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCA_CODEX_HOME", str(tmp_path))
    original = data_home()
    monkeypatch.setenv("ARCA_CODEX_PROFILE", "cliente-homo")
    named = data_home()
    assert named == original / "profiles" / "cliente-homo"
    assert credential_account(original, "homologacion", "20123456786") != credential_account(
        named, "homologacion", "20123456786"
    )
    monkeypatch.setenv("ARCA_CODEX_PROFILE", "../other")
    with pytest.raises(ValueError, match="Nombre de perfil"):
        data_home()


def test_existing_csr_keeps_legacy_keyring(tmp_path):
    folder = tmp_path / "credentials" / "homologacion" / "20123456786"
    folder.mkdir(parents=True)
    (folder / "request.csr").write_text("fixture")
    assert credential_account(tmp_path, "homologacion", "20123456786") == "homologacion:20123456786"


def test_delegation_needs_confirmation(profile):
    raw = json.loads(profile.model_dump_json())
    raw["certificate_cuit"] = "30712345671"
    with pytest.raises(ValidationError, match="delegación"):
        Profile.model_validate(raw)
    raw["delegation_confirmed"] = True
    assert Profile.model_validate(raw).issuer.cuit == profile.issuer.cuit


@pytest.mark.parametrize("kind", [1, 6])
def test_business_authorization_and_reconciliation(tmp_path, profile, invoice, arca, kind):
    p = business_profile(profile)
    data = business_invoice(invoice, kind)
    arca.iva_conditions = lambda voucher_class: [
        {"Id": data["recipient_iva_id"], "Desc": data["recipient_iva_label"]}
    ]
    arca.vat_rates = lambda: [{"Id": "5", "Desc": "21%"}]
    e = Engine(p, tmp_path, arca)
    draft = e.prepare(data, f"business-test-{kind:03d}")
    arca.fail = True
    result = e.emit(draft["draft_id"], draft["confirmation"])
    assert result["state"] == "uncertain"
    reconciled = e.reconcile(draft["draft_id"])
    assert reconciled["state"] == "authorized"
    render_invoice(reconciled["summary"], reconciled["result"], tmp_path / "authorized.pdf")
    assert arca.calls == 1
    assert e.emit(draft["draft_id"], draft["confirmation"])["state"] == "authorized"
    assert arca.calls == 1


def test_a_to_monotributista_legend(tmp_path, profile, invoice):
    data = business_invoice(invoice)
    data.update(recipient_iva_id=6, recipient_iva_label="Responsable Monotributo")
    summary = Engine(business_profile(profile), tmp_path, None).preview(data)
    path = tmp_path / "a-mono.pdf"
    render_invoice(summary, None, path)
    text = "\n".join(x.extract_text() for x in PdfReader(path).pages)
    assert "27.618" in text and "crédito fiscal" in text


@pytest.mark.parametrize(
    "point",
    [
        {"Nro": "9", "Bloqueado": "N", "FchBaja": "NULL"},
        {"Nro": "2", "Bloqueado": "S", "FchBaja": "NULL"},
        {"Nro": "2", "Bloqueado": "N", "FchBaja": "20260101"},
    ],
)
def test_doctor_rejects_wrong_or_blocked_point(tmp_path, profile, monkeypatch, point):
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from arca_codex import cli
    from arca_codex.storage import write_private

    write_private(tmp_path / "profile.json", profile.model_dump_json().encode())
    monkeypatch.setenv("ARCA_CODEX_HOME", str(tmp_path))
    monkeypatch.delenv("ARCA_CODEX_PROFILE", raising=False)
    monkeypatch.setattr("sys.argv", ["arca-codex", "doctor"])
    monkeypatch.setattr(
        cli, "load_pair", lambda p: (None, SimpleNamespace(not_valid_after_utc=datetime.now(UTC)))
    )
    monkeypatch.setattr("arca_codex.soap.Wsfe", lambda *a: SimpleNamespace(points=lambda: [point]))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 1
