import copy
import json
from types import SimpleNamespace

import pytest

from arca_codex import server
from arca_codex.engine import Engine
from arca_codex.soap import ArcaError, Wsfe


def args():
    return dict(
        environment="homologacion",
        issuer_cuit="20123456786",
        point_of_sale=2,
        voucher_type=211,
        number=8,
    )


@pytest.mark.parametrize("state", ["authorized", "prepared", "uncertain"])
def test_consult_always_calls_remote_and_never_changes_journal(
    tmp_path,
    profile,
    invoice,
    arca,
    monkeypatch,
    state,
):
    engine = Engine(profile, tmp_path, arca)
    draft = engine.prepare(invoice, "remote-demo-001")
    engine.set_state(draft["draft_id"], state, {"cae": "12345678901234"})
    before = engine.get(draft["draft_id"])
    journal = (tmp_path / "journal.sqlite3").read_bytes()
    calls = []
    remote = {
        "DocTipo": "80",
        "DocNro": invoice["recipient_cuit"],
        "ImpTotal": "123456.78",
        "CbteFch": "20261016",
        "CodAutorizacion": "12345678901234",
        "FchVto": "20261016",
        "Resultado": "A",
        "EmisionTipo": "CAE",
    }

    def consult(kind, number):
        calls.append((kind, number))
        return remote

    monkeypatch.setattr(arca, "consult", consult)
    # No journal access is available to the server tool, only profile and client.
    monkeypatch.setattr(server, "engine", lambda: SimpleNamespace(profile=profile, client=arca))
    for _ in range(2):
        reply = server.consult_invoice(**args())
        assert reply["source"] == "ARCA FECompConsultar"
        assert reply["found"] and reply["voucher"] == remote
        assert reply["environment"] == "homologacion" and reply["number"] == 8
        assert reply["queried_at"].endswith("+00:00")
    assert calls == [(211, 8), (211, 8)] and arca.calls == 0
    assert (tmp_path / "journal.sqlite3").read_bytes() == journal
    assert engine.get(draft["draft_id"]) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("environment", "produccion"),
        ("issuer_cuit", "30712345671"),
        ("point_of_sale", 3),
    ],
)
def test_scope_mismatch_does_not_contact_arca(profile, monkeypatch, field, value):
    def forbidden(*_):
        raise AssertionError("No debe consultar un perfil diferente")

    client = SimpleNamespace(consult=forbidden)
    monkeypatch.setattr(server, "engine", lambda: SimpleNamespace(profile=profile, client=client))
    with pytest.raises(ValueError, match="no coincide"):
        server.consult_invoice(**{**args(), field: value})


def test_not_found_is_remote_and_not_a_local_authorization(profile, monkeypatch):
    calls = []

    def consult(kind, number):
        calls.append((kind, number))
        return None

    client = SimpleNamespace(consult=consult)
    monkeypatch.setattr(server, "engine", lambda: SimpleNamespace(profile=profile, client=client))
    reply = server.consult_invoice(**args())
    assert calls == [(211, 8)] and reply["found"] is False and reply["voucher"] is None


def test_public_projection_excludes_authentication_and_arbitrary_nested_fields(
    profile, monkeypatch
):
    remote = {
        "DocNro": "30712345671",
        "ImpTotal": "123456.78",
        "Token": "SECRET_SENTINEL",
        "Sign": "SECRET_SENTINEL",
        "Auth": {"Token": "SECRET_SENTINEL"},
        "Errors": {"Msg": "SECRET_SENTINEL"},
        "FchVto": {"Token": "SECRET_SENTINEL"},
    }
    original = copy.deepcopy(remote)
    client = SimpleNamespace(consult=lambda *_: remote)
    monkeypatch.setattr(server, "engine", lambda: SimpleNamespace(profile=profile, client=client))
    reply = server.consult_invoice(**args())
    assert reply["voucher"] == {"DocNro": "30712345671", "ImpTotal": "123456.78"}
    assert "SECRET_SENTINEL" not in json.dumps(reply) and remote == original


def test_remote_errors_propagate_without_using_local_results(profile, monkeypatch):
    def consult(*_):
        raise ArcaError("ARCA rechazó la consulta: códigos 600", ["600"])

    monkeypatch.setattr(
        server,
        "engine",
        lambda: SimpleNamespace(profile=profile, client=SimpleNamespace(consult=consult)),
    )
    with pytest.raises(ArcaError) as error:
        server.consult_invoice(**args())
    assert error.value.codes == ("600",)


def test_consult_uses_fecompconsultar_and_the_configured_point(tmp_path, profile, monkeypatch):
    ws = Wsfe(profile, tmp_path)
    calls = []

    def call(method, payload):
        calls.append((method, payload))
        return {"ResultGet": {"CodAutorizacion": "12345678901234"}}

    monkeypatch.setattr(ws, "call", call)
    assert ws.consult(211, 8) == {"CodAutorizacion": "12345678901234"}
    assert calls == [
        ("FECompConsultar", {"FeCompConsReq": {"CbteTipo": 211, "CbteNro": 8, "PtoVta": 2}})
    ]
