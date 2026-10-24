from datetime import date

import pytest

from arca_codex import models, server
from arca_codex.engine import Engine


@pytest.mark.parametrize("invoice_date", ["2026-11-01", "2026-10-31"])
def test_expired_rule_blocks_before_authorization_even_backdated(
    tmp_path, profile, invoice, arca, monkeypatch, invoice_date
):
    monkeypatch.setattr(models, "today", lambda: date(2026, 11, 1))
    invoice = {**invoice, "invoice_date": invoice_date, "due_date": "2026-11-30"}
    profile = profile.model_copy(update={"environment": "produccion", "production_enabled": True})
    engine = Engine(profile, tmp_path, arca)
    draft = engine.prepare(invoice, "preflight-expired-001")
    with pytest.raises(ValueError, match="vigencia"):
        engine.emit(draft["draft_id"], draft["confirmation"], emission_place="Localidad de prueba")
    assert arca.calls == 0
    assert engine.get(draft["draft_id"])["state"] == "prepared"


@pytest.mark.parametrize("place", [None, "  ", "A" * 121, "Ciudad\nOtra"])
def test_missing_or_invalid_place_blocks_before_authorization(
    tmp_path, profile, invoice, arca, place
):
    engine = Engine(profile, tmp_path, arca)
    draft = engine.prepare(invoice, "preflight-place-001")
    with pytest.raises(ValueError, match="[Ll]ugar de emisión"):
        engine.emit(draft["draft_id"], draft["confirmation"], emission_place=place)
    assert arca.calls == 0
    assert engine.get(draft["draft_id"])["state"] == "prepared"


@pytest.mark.parametrize("uncertain", [False, True])
def test_confirmed_place_survives_authorization_reconciliation_and_reexport(
    tmp_path, profile, invoice, arca, monkeypatch, uncertain
):
    engine = Engine(profile, tmp_path, arca)
    draft = engine.prepare(invoice, "preflight-retain-001")
    arca.fail = uncertain
    out = engine.emit(
        draft["draft_id"], draft["confirmation"], emission_place="  Localidad de prueba  "
    )
    assert out["result"]["emission_place"] == "Localidad de prueba"
    if uncertain:
        out = engine.reconcile(draft["draft_id"])
    assert out["state"] == "authorized"
    assert out["result"]["emission_place"] == "Localidad de prueba"
    monkeypatch.setattr(server, "engine", lambda: engine)
    # Reexporting an authorized historical invoice does not use today's rule.
    monkeypatch.setattr(models, "today", lambda: date(2026, 11, 1))
    assert server.export_invoice_pdf(draft["draft_id"])["cae"] == out["result"]["cae"]
    with pytest.raises(ValueError, match="difiere"):
        server.export_invoice_pdf(draft["draft_id"], emission_place="Otra localidad")
    assert arca.calls == 1


def test_common_c_is_not_subject_to_fce_deadline_or_place(
    tmp_path, profile, invoice, arca, monkeypatch
):
    monkeypatch.setattr(models, "today", lambda: date(2026, 11, 1))
    invoice = {
        **invoice,
        "voucher_type": 11,
        "invoice_date": "2026-11-01",
        "due_date": "2026-11-30",
        "cbu": None,
        "transfer": None,
    }
    engine = Engine(profile, tmp_path, arca)
    draft = engine.prepare(invoice, "preflight-common-001")
    original = arca.authorize

    def authorize(kind, detail):
        reply = original(kind, detail)
        reply["FeDetResp"]["FECAEDetResponse"]["CAEFchVto"] = "20261111"
        return reply

    monkeypatch.setattr(arca, "authorize", authorize)
    out = engine.emit(draft["draft_id"], draft["confirmation"])
    assert out["state"] == "authorized"
    assert "emission_place" not in out["result"]
    assert arca.calls == 1
