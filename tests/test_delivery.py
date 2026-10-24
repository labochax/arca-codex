import base64
import copy
import json
from email import policy
from email.parser import BytesParser
from pathlib import Path

import pytest
from pypdf import PdfReader

from arca_codex import delivery, server
from arca_codex.engine import Engine


@pytest.fixture
def issued(tmp_path, profile, invoice, arca, monkeypatch):
    profile = profile.model_copy(update={"environment": "produccion", "production_enabled": True})
    root = tmp_path / "private"
    root.mkdir()
    engine = Engine(profile, root, arca)
    draft = engine.prepare(invoice, "delivery-demo-001")
    engine.emit(draft["draft_id"], draft["confirmation"], emission_place="Localidad de prueba")
    monkeypatch.setattr(server, "engine", lambda: engine)
    monkeypatch.setattr(server, "data_home", lambda: root)
    return engine, draft["draft_id"], arca


def test_names_use_service_period_and_arca_identity(issued):
    engine, draft, _ = issued
    summary = engine.get(draft)["payload"]
    assert delivery.pdf_filename(summary) == "2026-09 - sep - 20123456786_211_00002_00000008.pdf"
    assert delivery.pdf_filename(summary, draft=True).startswith("BORRADOR - 2026-09 - sep - ")
    other = copy.deepcopy(summary)
    other["environment"] = "homologacion"
    assert delivery.pdf_filename(other).startswith("HOMOLOGACION - ")
    other["invoice"]["voucher_type"] = 11
    assert "_011_" in delivery.pdf_filename(other)


def test_unprepared_preview_keeps_an_explicit_nonfiscal_filename(issued, invoice):
    _, _, arca = issued
    preview = server.preview_invoice(invoice, "Localidad de prueba")
    filename = Path(preview["pdf_path"]).name
    assert filename.startswith("BORRADOR - 2026-09 - sep - ")
    assert "SIN-NUMERO-" in filename and arca.calls == 1


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2024-02-01", "2024-02-29", "2024-02 - feb"),
        ("2026-09-03", "2026-09-30", "2026-09-03_al_2026-09-30"),
        ("2026-12-01", "2027-01-31", "2026-12-01_al_2027-01-31"),
    ],
)
def test_partial_and_cross_month_periods_are_not_truncated(issued, start, end, expected):
    engine, draft, _ = issued
    summary = copy.deepcopy(engine.get(draft)["payload"])
    summary["invoice"].update(service_from=start, service_to=end)
    assert delivery.pdf_filename(summary).startswith(expected + " - ")


@pytest.mark.parametrize(
    "period",
    [
        "../../secret",
        "2026-13",
        "2026-02-30",
        "",
        "2026-09_al_other",
        "2026-10-01_al_2026-09-01",
        "2026-09\nBcc:other@example.invalid",
    ],
)
def test_filing_date_cannot_be_a_path_or_invalid_date(issued, period):
    engine, draft, _ = issued
    with pytest.raises(ValueError):
        delivery.pdf_filename(engine.get(draft)["payload"], period=period)


def test_confirmed_save_remembers_directory_but_does_not_touch_fiscal_data(issued, tmp_path):
    engine, draft, arca = issued
    archive = tmp_path / "archive"
    archive.mkdir()
    before = engine.get(draft)
    journal = (engine.root / "journal.sqlite3").read_bytes()
    out = server.export_invoice_pdf(draft)
    assert Path(out["pdf_path"]).name.startswith("2026-09 - sep - ")
    assert out["requires_output_confirmation"]
    plan = server.invoice_output_options(draft, "2026-10", str(archive))
    assert plan["default_filing_period"] == "2026-09" and plan["directory_exists"]
    assert plan["requires_period_confirmation"] and plan["requires_directory_confirmation"]
    with pytest.raises(ValueError, match="Confirmá"):
        server.save_invoice_pdf(draft, plan["confirmation"], "2026-09", str(archive))
    assert list(archive.iterdir()) == []
    saved = server.save_invoice_pdf(draft, plan["confirmation"], "2026-10", str(archive), True)
    assert Path(saved["pdf_path"]).is_file()
    assert Path(saved["pdf_path"]).name.startswith("2026-10 - oct - ")
    assert saved["cloud_sync_verified"] is False and saved["email_sent"] is False
    again = server.save_invoice_pdf(draft, plan["confirmation"], "2026-10", str(archive))
    assert again["already_present"] and again["sha256"] == saved["sha256"]
    assert server.invoice_output_options(draft)["output_directory"] == str(archive)
    assert engine.get(draft) == before and arca.calls == 1
    assert (engine.root / "journal.sqlite3").read_bytes() == journal
    preferences = json.loads((engine.root / "delivery-preferences.json").read_text())
    assert set(preferences) == {"default_output_directory"}


def test_existing_different_file_is_never_overwritten(issued, tmp_path):
    _, draft, arca = issued
    archive = tmp_path / "archive"
    archive.mkdir()
    plan = server.invoice_output_options(draft, output_directory=str(archive))
    target = archive / plan["filename"]
    target.write_bytes(b"existing user document")
    with pytest.raises(ValueError, match="no se sobrescribió"):
        server.save_invoice_pdf(draft, plan["confirmation"], output_directory=str(archive))
    assert target.read_bytes() == b"existing user document" and arca.calls == 1


def test_reexport_never_overwrites_the_default_archive_or_approved_email(issued, monkeypatch):
    engine, draft, _ = issued
    plan = server.invoice_output_options(draft)
    saved = server.save_invoice_pdf(draft, plan["confirmation"])
    archived = Path(saved["pdf_path"])
    original = archived.read_bytes()
    proposal = server.prepare_invoice_email(
        draft, "Test", "contact@example.invalid", sender_email="issuer@example.invalid"
    )
    real_renderer = server.render_invoice

    def changed_timestamp(*args, **kwargs):
        path = real_renderer(*args, **kwargs)
        with Path(path).open("ab") as stream:
            stream.write(b"\n% different render timestamp\n")
        return path

    monkeypatch.setattr(server, "render_invoice", changed_timestamp)
    reexport = server.export_invoice_pdf(draft)
    assert Path(reexport["pdf_path"]).read_bytes() != original
    assert archived.read_bytes() == original
    handoff = server.invoice_email_handoff(proposal["proposal_id"], proposal["confirmation"])
    assert handoff["ready_for_gmail"]
    assert Path(handoff["attachment_path"]).read_bytes() == archived.read_bytes()
    assert engine.get(draft)["state"] == "authorized"


def test_unavailable_directory_preserves_private_pdf(issued, tmp_path):
    _, draft, arca = issued
    private = server.export_invoice_pdf(draft)["pdf_path"]
    unavailable = tmp_path / "unmounted" / "invoices"
    plan = server.invoice_output_options(draft, output_directory=str(unavailable))
    with pytest.raises(ValueError, match="carpeta no está disponible"):
        server.save_invoice_pdf(draft, plan["confirmation"], output_directory=str(unavailable))
    assert Path(private).is_file() and not unavailable.exists() and arca.calls == 1


def test_prepared_output_can_be_proposed_but_not_archived(issued, invoice):
    engine, _, _ = issued
    another = dict(invoice, total="234567.89")
    draft = engine.prepare(another, "another-demo")
    plan = server.invoice_output_options(draft["draft_id"])
    with pytest.raises(ValueError, match="autorizado"):
        server.save_invoice_pdf(draft["draft_id"], plan["confirmation"])


def test_paths_reject_git_credentials_relative_and_symlink_target(issued, tmp_path):
    engine, draft, _ = issued
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    for path in (str(repo), str(engine.root), str(engine.root / "credentials"), "relative"):
        with pytest.raises(ValueError):
            server.invoice_output_options(draft, output_directory=path)
    if hasattr(Path, "symlink_to"):
        archive = tmp_path / "archive"
        archive.mkdir()
        plan = server.invoice_output_options(draft, output_directory=str(archive))
        other = tmp_path / "other.pdf"
        other.write_bytes(b"keep")
        try:
            (archive / plan["filename"]).symlink_to(other)
        except OSError:
            pytest.skip("Symlink creation unavailable for this OS/account")
        with pytest.raises(ValueError, match="no se sobrescribió"):
            server.save_invoice_pdf(draft, plan["confirmation"], output_directory=str(archive))
        assert other.read_bytes() == b"keep"


def test_email_asks_for_contact_and_never_assumes_the_legal_entity(issued):
    _, draft, arca = issued
    out = server.prepare_invoice_email(draft)
    assert out["missing"] == ["recipient_name", "recipient_email"] and out["sent"] is False
    assert arca.calls == 1


def test_email_payload_attaches_exact_saved_pdf_and_paid_notice(issued, tmp_path):
    engine, draft, arca = issued
    archive = tmp_path / "archive"
    archive.mkdir()
    plan = server.invoice_output_options(draft, output_directory=str(archive))
    saved = server.save_invoice_pdf(draft, plan["confirmation"], output_directory=str(archive))
    before = engine.get(draft)
    proposal = server.prepare_invoice_email(
        draft,
        "Contacto de prueba",
        "contact@example.invalid",
        sender_email="issuer@example.invalid",
    )
    assert proposal["sent"] is False and proposal["requires_send_confirmation"] is True
    assert "pago recibido el 09-10-2026" in proposal["body"]
    assert "01-09-2026 al 30-09-2026" in proposal["body"]
    payload = json.loads(Path(proposal["gmail_payload_path"]).read_text())
    attachment = payload["payload"]["parts"][1]
    assert (
        base64.urlsafe_b64decode(attachment["body"]["base64_url_content"])
        == Path(saved["pdf_path"]).read_bytes()
    )
    assert attachment["filename"] == Path(saved["pdf_path"]).name
    message = BytesParser(policy=policy.default).parsebytes(Path(proposal["eml_path"]).read_bytes())
    assert message["To"].addresses[0].addr_spec == "contact@example.invalid"
    assert (
        list(message.iter_attachments())[0].get_payload(decode=True)
        == Path(saved["pdf_path"]).read_bytes()
    )
    with pytest.raises(ValueError, match="Confirmación"):
        server.invoice_email_handoff(proposal["proposal_id"], "wrong")
    handoff = server.invoice_email_handoff(proposal["proposal_id"], proposal["confirmation"])
    assert handoff["ready_for_gmail"] is True and handoff["sent"] is False
    assert engine.get(draft) == before and arca.calls == 1


def test_email_cannot_be_handed_off_without_a_verified_sender(issued):
    _, draft, _ = issued
    proposal = server.prepare_invoice_email(draft, "Test", "contact@example.invalid")
    with pytest.raises(ValueError, match="cuenta Gmail"):
        server.invoice_email_handoff(proposal["proposal_id"], proposal["confirmation"])


def test_email_freezes_an_unarchived_attachment_before_reexport(issued):
    _, draft, _ = issued
    proposal = server.prepare_invoice_email(
        draft, "Test", "contact@example.invalid", sender_email="issuer@example.invalid"
    )
    attachment = Path(proposal["attachment_path"])
    original = attachment.read_bytes()
    out = server.export_invoice_pdf(draft)
    assert Path(out["pdf_path"]) != attachment
    Path(out["pdf_path"]).write_bytes(b"another render")
    assert attachment.read_bytes() == original
    assert server.invoice_email_handoff(proposal["proposal_id"], proposal["confirmation"])[
        "ready_for_gmail"
    ]


@pytest.mark.parametrize("field", ["attachment", "payload"])
def test_changed_attachment_or_email_requires_new_review(issued, field):
    _, draft, _ = issued
    proposal = server.prepare_invoice_email(
        draft, "Test", "contact@example.invalid", sender_email="issuer@example.invalid"
    )
    path = proposal["attachment_path" if field == "attachment" else "gmail_payload_path"]
    Path(path).write_bytes(b"changed after review")
    with pytest.raises(ValueError, match="cambió"):
        server.invoice_email_handoff(proposal["proposal_id"], proposal["confirmation"])


@pytest.mark.parametrize(
    "name,email",
    [
        ("Person\nBcc: other@example.invalid", "contact@example.invalid"),
        ("Person", "contact@example.invalid\r\nBcc: other@example.invalid"),
        ("Person", "one@example.invalid,two@example.invalid"),
        ("Person", "not an email"),
    ],
)
def test_email_rejects_header_injection_and_recipient_lists(issued, name, email):
    _, draft, _ = issued
    with pytest.raises(ValueError):
        server.prepare_invoice_email(draft, name, email)


def test_localized_pdf_dates_do_not_mutate_iso_record(issued):
    engine, draft, _ = issued
    before = engine.get(draft)
    out = server.export_invoice_pdf(draft)
    text = PdfReader(out["pdf_path"]).pages[0].extract_text()
    assert "Fecha de emisión: 16-10-2026" in text
    assert "Inicio de actividades: 01-01-2024" in text
    assert "01-09-2026 al 30-09-2026" in text
    assert "Vencimiento CAE: 16-10-2026" in text
    assert (
        engine.get(draft) == before and before["payload"]["invoice"]["invoice_date"] == "2026-10-16"
    )
