import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from arca_codex.engine import Engine
from arca_codex.models import Invoice, cbu_valid, cuit_valid
from arca_codex.storage import database


@pytest.mark.parametrize("total", ["1,25", "1.234,56", "-1", "0", "NaN", "Infinity", "1.001", 1.2])
def test_reject_ambiguous_or_nonexact_amounts(invoice, total):
    with pytest.raises(ValueError):
        Invoice.model_validate({**invoice, "total": total})


def test_fce_window_is_not_ordinary_services_window(invoice):
    fce = Invoice.model_validate({**invoice, "invoice_date": "2026-10-09"})
    with pytest.raises(ValueError, match="ventana"):
        fce.validate_issue_date(date(2026, 10, 16))
    common = Invoice.model_validate(
        {**invoice, "voucher_type": 11, "cbu": None, "transfer": None, "invoice_date": "2026-10-09"}
    )
    common.validate_issue_date(date(2026, 10, 16))


def test_fce_requires_fields_and_zero_vat(invoice):
    d = Invoice.model_validate(invoice).detail(8)
    assert d["ImpTotal"] == d["ImpNeto"] == "123456.78"
    assert d["ImpIVA"] == "0.00" and "Iva" not in d
    assert d["Opcionales"]["Opcional"] == [
        {"Id": "2101", "Valor": "0000000000000000000000"},
        {"Id": "27", "Valor": "SCA"},
        {"Id": "23", "Valor": "DEMO-SEP-26"},
    ]
    assert "description" not in d
    with pytest.raises(ValueError):
        Invoice.model_validate({**invoice, "cbu": None})
    with pytest.raises(ValueError):
        Invoice.model_validate({**invoice, "due_date": "2026-10-09"})
    with pytest.raises(ValueError):
        Invoice.model_validate({**invoice, "service_to": "2026-08-31"})


def test_identifiers_validate_check_digits():
    assert cuit_valid("20123456786")
    with pytest.raises(ValueError):
        cuit_valid("20123456780")
    with pytest.raises(ValueError):
        cbu_valid("0000000100000000000000")


def test_prepare_never_emits_and_replay_is_idempotent(tmp_path, profile, invoice, arca):
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    b = e.prepare(invoice, "monthly-demo-001")
    assert a == b and arca.calls == 0
    assert a["summary"]["number"] == 8
    with pytest.raises(ValueError):
        e.emit(a["draft_id"], "yes")
    assert arca.calls == 0
    assert (
        e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")["state"]
        == "authorized"
    )
    e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    assert arca.calls == 1


def test_duplicate_with_changed_description_or_date_is_blocked(tmp_path, profile, invoice, arca):
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    with pytest.raises(ValueError):
        e.prepare(
            {**invoice, "description": "retry", "invoice_date": "2026-10-15"}, "monthly-demo-002"
        )
    assert arca.calls == 1


def test_production_disabled_by_default(tmp_path, profile, invoice, arca):
    e = Engine(profile.model_copy(update={"environment": "produccion"}), tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    with pytest.raises(ValueError, match="deshabilitada"):
        e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    assert arca.calls == 0


def test_concurrent_emit_cannot_duplicate(tmp_path, profile, invoice, arca):
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: e.emit(
                    a["draft_id"], a["confirmation"], emission_place="Localidad de prueba"
                ),
                range(2),
            )
        )
    assert [r["state"] for r in results] == ["authorized", "authorized"]
    assert arca.calls == 1


def test_timeout_after_authorization_requires_consult_not_retry(tmp_path, profile, invoice, arca):
    arca.fail = True
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    assert (
        e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")["state"]
        == "uncertain"
    )
    with pytest.raises(ValueError):
        e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    assert e.reconcile(a["draft_id"])["result"]["reconciled"]
    assert arca.calls == 1


def test_uncertain_blocks_other_invoices(tmp_path, profile, invoice, arca):
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    arca.fail = True
    e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    with pytest.raises(ValueError, match="conciliación"):
        e.prepare({**invoice, "total": "234567.89"}, "monthly-demo-002")
    arca.remote = None
    assert e.reconcile(a["draft_id"])["state"] == "uncertain"
    assert arca.calls == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("ImpTotal", "12.00"),
        ("DocNro", "20123456786"),
        ("FchServHasta", "20260929"),
        ("MonId", "DOL"),
    ],
)
def test_reconciliation_checks_fiscal_identity(tmp_path, profile, invoice, arca, field, value):
    arca.fail = True
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    arca.remote[field] = value
    with pytest.raises(ValueError, match="difiere"):
        e.reconcile(a["draft_id"])
    assert e.get(a["draft_id"])["state"] == "uncertain"


def test_stale_sequence_requires_new_preparation(tmp_path, profile, invoice, arca):
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    arca.number = 8
    with pytest.raises(ValueError, match="numeración"):
        e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    b = e.prepare(invoice, "monthly-demo-002")
    assert b["summary"]["number"] == 9 and b["draft_id"] != a["draft_id"]
    assert arca.calls == 0


def test_tampered_draft_cannot_be_emitted(tmp_path, profile, invoice, arca):
    e = Engine(profile, tmp_path, arca)
    a = e.prepare(invoice, "monthly-demo-001")
    with database(tmp_path) as db:
        row = db.execute("SELECT payload FROM drafts").fetchone()
        payload = json.loads(row[0])
        payload["invoice"]["total"] = "1.00"
        db.execute("UPDATE drafts SET payload=?", (json.dumps(payload),))
        db.commit()
    with pytest.raises(ValueError, match="hash"):
        e.emit(a["draft_id"], a["confirmation"], emission_place="Localidad de prueba")
    assert arca.calls == 0
