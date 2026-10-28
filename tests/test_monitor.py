import json
from datetime import date

import pytest
from pydantic import ValidationError

from arca_codex.engine import Engine
from arca_codex.models import Invoice
from arca_codex.monitor import (
    LIMITS,
    RevenueEntry,
    RevenuePolicy,
    configure,
    import_entries,
    report,
    window_start,
)
from arca_codex.storage import database


def production(profile):
    return profile.model_copy(update={"environment": "produccion", "production_enabled": True})


def entry(number=1, total="100.00", when="2026-10-01", **kwargs):
    return RevenueEntry(
        issuer_cuit="20123456786",
        voucher_type=11,
        point_of_sale=1,
        number=number,
        invoice_date=when,
        total=total,
        source="CSV prueba",
        **kwargs,
    )


def setup_policy(root, p, category="A", **kwargs):
    return configure(
        root,
        p,
        RevenuePolicy(
            category=category,
            coverage_from="2025-10-01",
            coverage_through="2026-10-31",
            coverage_confirmed=True,
            **kwargs,
        ),
    )


@pytest.mark.parametrize("category", list("ABCDEFGHIJK"))
def test_all_categories_are_available(tmp_path, profile, category):
    setup_policy(tmp_path, profile, category)
    status = report(tmp_path, profile)
    assert status["official_category_limit"] == str(LIMITS[category])
    assert status["coverage_confirmed"]


def test_missing_history_is_never_a_green_light(tmp_path, profile):
    assert report(tmp_path, profile)["configured"] is False
    configure(tmp_path, profile, RevenuePolicy(category="C"))
    result = report(tmp_path, profile)
    assert not result["coverage_confirmed"]
    assert "incompleto" in result["warnings"][0]


def test_rolling_window_and_all_points_imported(tmp_path, profile):
    p = production(profile)
    setup_policy(tmp_path, p)
    import_entries(
        tmp_path,
        p,
        [
            entry(1, "100.00", "2025-10-16"),
            entry(2, "200.00", "2025-10-17"),
            entry(3, "300.00", "2026-10-16"),
            entry(4, "999.00", "2026-10-17"),
        ],
    )
    result = report(tmp_path, p)
    assert result["window_from"] == "2025-10-17"
    assert result["known_gross_revenue"] == "500.00"
    assert window_start(date(2024, 2, 29)) == date(2023, 3, 1)


def test_import_is_atomic_and_idempotent(tmp_path, profile):
    p = production(profile)
    assert import_entries(tmp_path, p, [entry()])["imported"] == 1
    assert import_entries(tmp_path, p, [entry()])["already_present"] == 1
    with pytest.raises(ValueError, match="diferentes"):
        import_entries(tmp_path, p, [entry(2), entry(1, "101.00")])
    setup_policy(tmp_path, p)
    assert report(tmp_path, p)["known_gross_revenue"] == "100.00"


def test_foreign_cuit_and_homologation_rejected(tmp_path, profile):
    with pytest.raises(ValueError, match="producción"):
        import_entries(tmp_path, profile, [entry()])
    other = entry().model_copy(update={"issuer_cuit": "30712345671"})
    with pytest.raises(ValueError, match="CUIT"):
        import_entries(tmp_path, production(profile), [other])


def test_import_cannot_mislabel_credit_to_reduce_income(tmp_path, profile):
    wrong = entry().model_copy(update={"kind": "credit"})
    with pytest.raises(ValueError, match="tipo de comprobante"):
        import_entries(tmp_path, production(profile), [wrong])


def test_no_silent_credit_deduction(tmp_path, profile):
    p = production(profile)
    setup_policy(tmp_path, p)
    credit = entry().model_copy(update={"kind": "credit", "voucher_type": 13})
    import_entries(tmp_path, p, [entry(), credit])
    result = report(tmp_path, p)
    assert result["known_gross_revenue"] == "100.00"
    assert result["credit_notes_not_deducted"] == "100.00"
    assert any("Notas de crédito" in x for x in result["warnings"])


def test_projection_and_critical_limit(tmp_path, profile, invoice):
    p = production(profile)
    setup_policy(tmp_path, p)
    import_entries(tmp_path, p, [entry(total="12000000.00")])
    result = report(tmp_path, p, candidate=Invoice(**invoice), monthly_projection="2000000.00")
    assert result["projected_gross"] == "12123456.78"
    assert any("recategorización" in x for x in result["warnings"])
    assert result["run_rate_scenario"]["annualized_amount"] == "24000000.00"
    import_entries(tmp_path, p, [entry(2, total="130000000.00")])
    assert report(tmp_path, p)["blocked"]


def test_check_inside_emission_and_refresh_confirmation(tmp_path, profile, invoice, arca):
    from arca_codex.monitor import check_issue

    p = production(profile)
    setup_policy(tmp_path, p, personal_limit="100000.00")
    inv = Invoice(**invoice)
    result = report(tmp_path, p, candidate=inv)
    with pytest.raises(ValueError, match="advertencias"):
        check_issue(tmp_path, p, inv, None)
    check_issue(tmp_path, p, inv, result["confirmation"])
    import_entries(tmp_path, p, [entry()])
    with pytest.raises(ValueError, match="advertencias"):
        check_issue(tmp_path, p, inv, result["confirmation"])
    e = Engine(p, tmp_path, arca)
    draft = e.prepare(invoice, "monitor-test-001")
    with pytest.raises(ValueError, match="advertencias"):
        e.emit(draft["draft_id"], draft["confirmation"], emission_place="Ciudad de prueba")
    assert arca.calls == 0


def test_revenue_not_double_counted_and_uncertain_counted(tmp_path, profile, invoice):
    p = production(profile)
    setup_policy(tmp_path, p)
    summary = {
        "environment": "produccion",
        "issuer": p.issuer.model_dump(mode="json"),
        "point_of_sale": 1,
        "number": 1,
        "invoice": {**invoice, "voucher_type": 11},
    }
    with database(tmp_path) as db:
        for key, state in [("a", "authorized"), ("b", "uncertain")]:
            db.execute(
                "INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    f"produccion:{p.issuer.cuit}:1",
                    key,
                    key,
                    json.dumps(summary),
                    state,
                    1,
                    None,
                    "2026-10-16",
                ),
            )
        db.commit()
    local = entry(total=invoice["total"], when=invoice["invoice_date"])
    import_entries(tmp_path, p, [local])
    result = report(tmp_path, p)
    assert result["known_gross_revenue"] == invoice["total"]
    assert result["uncertain_exposure"] == invoice["total"]
    assert result["projected_gross"] == "246913.56"


def test_conflict_and_stale_scale_block(tmp_path, profile, monkeypatch):
    setup_policy(tmp_path, profile)
    monkeypatch.setattr("arca_codex.models.today", lambda: date(2027, 2, 1))
    assert report(tmp_path, profile)["blocked"]


def test_policy_invalid_and_foreign(tmp_path, profile):
    with pytest.raises(ValidationError):
        RevenuePolicy(category="L")
    with pytest.raises(ValueError, match="fechas"):
        configure(tmp_path, profile, RevenuePolicy(category="A", coverage_confirmed=True))
    setup_policy(tmp_path, profile)
    other = production(profile)
    other.issuer = other.issuer.model_copy(update={"cuit": "30712345671"})
    with pytest.raises(ValueError, match="otro CUIT"):
        report(tmp_path, other)


def test_incomplete_coverage_cannot_be_acknowledged_away(tmp_path, profile, invoice):
    from arca_codex.monitor import check_issue

    p = production(profile)
    configure(tmp_path, p, RevenuePolicy(category="K"))
    status = report(tmp_path, p, candidate=Invoice(**invoice))
    assert status["blocked"]
    with pytest.raises(ValueError, match="bloqueado"):
        check_issue(tmp_path, p, Invoice(**invoice), status["confirmation"])


def test_reading_status_does_not_create_journal(tmp_path, profile):
    setup_policy(tmp_path, profile)
    report(tmp_path, profile)
    assert not (tmp_path / "journal.sqlite3").exists()
