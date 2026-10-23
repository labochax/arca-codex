import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from arca_codex.engine import Engine
from arca_codex.soap import ArcaError, TransportError, Wsfe


@pytest.mark.parametrize(
    "environment,codes,empty_catalog",
    [
        ("homologacion", ["602"], True),
        ("produccion", ["602"], False),
        ("homologacion", ["600"], False),
        ("homologacion", ["602", "600"], False),
    ],
)
def test_catalog_error_scope(tmp_path, profile, monkeypatch, environment, codes, empty_catalog):
    ws = Wsfe(profile.model_copy(update={"environment": environment}), tmp_path)

    def fail(method):
        assert method == "FEParamGetPtosVenta"
        raise ArcaError("simulated", codes)

    monkeypatch.setattr(ws, "call", fail)
    if empty_catalog:
        assert ws.points() == []
    else:
        with pytest.raises(ArcaError):
            ws.points()


def test_homologation_empty_catalog_still_requires_last_query(
    tmp_path, profile, invoice, arca, monkeypatch
):
    monkeypatch.setattr(arca, "points", lambda: [])
    engine = Engine(profile, tmp_path, arca)

    def fail(kind):
        raise ArcaError("last-number query rejected", ["600"])

    monkeypatch.setattr(arca, "last", fail)
    with pytest.raises(ArcaError):
        engine.prepare(invoice, "catalog-demo-001")
    monkeypatch.setattr(arca, "last", lambda kind: 0)
    draft = engine.prepare(invoice, "catalog-demo-001")
    assert draft["state"] == "prepared"
    assert draft["summary"]["number"] == 1
    assert arca.calls == 0


@pytest.mark.parametrize(
    "environment,catalog",
    [
        ("homologacion", "blocked"),
        ("homologacion", "different"),
        ("produccion", "blocked"),
        ("produccion", "different"),
        ("produccion", "empty"),
    ],
)
def test_catalog_does_not_hide_blocked_or_missing_points(
    tmp_path, profile, invoice, arca, monkeypatch, environment, catalog
):
    points = {
        "blocked": [{"Nro": "2", "Bloqueado": "S"}],
        "different": [{"Nro": "3", "Bloqueado": "N"}],
        "empty": [],
    }[catalog]
    monkeypatch.setattr(arca, "points", lambda: points)
    engine = Engine(profile.model_copy(update={"environment": environment}), tmp_path, arca)
    with pytest.raises(ValueError, match="Punto de venta"):
        engine.prepare(invoice, "catalog-demo-001")
    assert arca.calls == 0


@pytest.mark.parametrize(
    "response",
    [
        {"CbteNro": "0"},
        {"PtoVta": "3", "CbteTipo": "211", "CbteNro": "0"},
        {"PtoVta": "2", "CbteTipo": "11", "CbteNro": "0"},
        {"PtoVta": "2", "CbteTipo": "211", "CbteNro": "-1"},
    ],
)
def test_last_query_rejects_incomplete_or_different_response(
    tmp_path, profile, monkeypatch, response
):
    ws = Wsfe(profile, tmp_path)
    monkeypatch.setattr(ws, "call", lambda *args: response)
    with pytest.raises(TransportError):
        ws.last(211)


def test_last_query_accepts_zero_with_matching_identity(tmp_path, profile, monkeypatch):
    ws = Wsfe(profile, tmp_path)
    monkeypatch.setattr(
        ws, "call", lambda *args: {"PtoVta": "2", "CbteTipo": "211", "CbteNro": "0"}
    )
    assert ws.last(211) == 0


def test_doctor_reports_unavailable_catalog_without_claiming_authorization(
    tmp_path, profile, monkeypatch, capsys
):
    from arca_codex import cli

    monkeypatch.setattr(cli, "data_home", lambda: tmp_path)
    monkeypatch.setattr(cli, "load_profile", lambda root: profile)
    monkeypatch.setattr(
        cli, "load_pair", lambda p: (None, SimpleNamespace(not_valid_after_utc=datetime.now(UTC)))
    )
    monkeypatch.setattr(cli.sys, "argv", ["arca-codex", "doctor"])
    calls = []

    def call(self, method, params=None, **kwargs):
        calls.append(method)
        if method == "FEParamGetPtosVenta":
            raise ArcaError("no records", ["602"])
        if method == "FECompUltimoAutorizado":
            assert params == {"PtoVta": 2, "CbteTipo": 211}
            return {"PtoVta": "2", "CbteTipo": "211", "CbteNro": "0"}
        if method == "FEDummy":
            return {"AppServer": "OK", "DbServer": "OK", "AuthServer": "OK"}
        if method == "FEParamGetCondicionIvaReceptor":
            return {"ResultGet": {"CondicionIvaReceptor": {"Id": "1"}}}
        pytest.fail(f"unexpected operation: {method}")

    monkeypatch.setattr(Wsfe, "call", call)
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert result["points_catalog_available"] is False
    assert result["last_fce_number"] == 0
    assert "no acredita" in result["notice"]
    assert "FECompUltimoAutorizado" in calls
