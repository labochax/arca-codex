"""Local revenue monitor. Never a certificate of tax compliance or an ARCA filing."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from filelock import FileLock
from pydantic import Field, field_validator

from . import models
from .models import Invoice, StrictModel, cuit_valid, digest
from .storage import database, write_private

SOURCE = "https://www.arca.gob.ar/monotributo/categorias.asp"
EFFECTIVE_FROM = date(2026, 8, 1)
REVIEW_BY = date(2027, 1, 31)  # Conservative software review deadline, not a statutory end date.
LIMITS = dict(
    zip(
        "ABCDEFGHIJK",
        map(
            Decimal,
            (
                "12009410.45",
                "17595182.74",
                "24670494.31",
                "30628651.43",
                "36028231.33",
                "45151659.41",
                "53995798.87",
                "81924660.37",
                "91699761.90",
                "105012519.20",
                "126610838.75",
            ),
        ),
    )
)


class RevenuePolicy(StrictModel):
    category: Literal["A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K"]
    warning_percent: int = Field(default=80, ge=1, le=100)
    personal_limit: str | None = None
    coverage_from: date | None = None
    coverage_through: date | None = None
    coverage_confirmed: bool = False

    @field_validator("personal_limit")
    @classmethod
    def amount(cls, value):
        return Invoice.money(value) if value is not None else None


def configure(root, profile, policy):
    if profile.issuer.tax_regime != "monotributo":
        raise ValueError("El control de categorías corresponde solo a monotributo")
    if policy.coverage_confirmed and (
        not policy.coverage_from
        or not policy.coverage_through
        or policy.coverage_from > policy.coverage_through
    ):
        raise ValueError("La cobertura confirmada requiere fechas válidas desde/hasta")
    data = {"issuer_cuit": profile.issuer.cuit, "policy": policy.model_dump(mode="json")}
    write_private(root / "revenue-policy.json", json.dumps(data).encode())
    return {"configured": True, **data, "notice": "No modifica la categoría registrada en ARCA"}


class RevenueEntry(StrictModel):
    issuer_cuit: str
    environment: Literal["produccion"] = "produccion"
    voucher_type: int = Field(ge=1, le=999)
    point_of_sale: int = Field(ge=1, le=99999)
    number: int = Field(ge=1, le=99999999)
    invoice_date: date
    total: str
    currency: Literal["ARS"] = "ARS"
    kind: Literal["invoice", "debit", "credit"] = "invoice"
    source: str = Field(min_length=1, max_length=160)
    _cuit = field_validator("issuer_cuit")(cuit_valid)

    @field_validator("total")
    @classmethod
    def validate_total(cls, value):
        return Invoice.money(value)

    @property
    def identity(self):
        return f"{self.issuer_cuit}:{self.voucher_type}:{self.point_of_sale}:{self.number}"


def import_entries(root, profile, entries):
    if profile.environment != "produccion":
        raise ValueError("El historial real se importa únicamente en un perfil de producción")
    with FileLock(str(root / "revenue.lock")), database(root) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS revenue_imports (identity TEXT PRIMARY KEY, data TEXT NOT NULL)"
        )
        imported = 0
        for entry in entries:
            if entry.issuer_cuit != profile.issuer.cuit:
                raise ValueError("CUIT importado diferente al perfil; no se mezclan contribuyentes")
            credit_types = {3, 8, 13, 53, 203, 208, 213}
            debit_types = {2, 7, 12, 52, 202, 207, 212}
            expected_kind = (
                "credit"
                if entry.voucher_type in credit_types
                else "debit"
                if entry.voucher_type in debit_types
                else "invoice"
            )
            if entry.kind != expected_kind:
                raise ValueError("El tipo de comprobante no coincide con invoice/debit/credit")
            value = entry.model_dump_json()
            previous = db.execute(
                "SELECT data FROM revenue_imports WHERE identity=?", (entry.identity,)
            ).fetchone()
            if previous and json.loads(previous[0]) != json.loads(value):
                raise ValueError("Comprobante ya importado con datos diferentes; revisar origen")
            if not previous:
                db.execute("INSERT INTO revenue_imports VALUES (?,?)", (entry.identity, value))
                imported += 1
        db.commit()
    return {
        "imported": imported,
        "already_present": len(entries) - imported,
        "fiscal_journal_modified": False,
    }


def window_start(as_of):
    try:
        anniversary = as_of.replace(year=as_of.year - 1)
    except ValueError:
        anniversary = as_of.replace(year=as_of.year - 1, day=28)
    return anniversary + timedelta(days=1)


@contextmanager
def read_journal(root):
    path = root / "journal.sqlite3"
    db = (
        sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        if path.exists()
        else sqlite3.connect(":memory:")
    )
    db.row_factory = sqlite3.Row
    if not path.exists():
        db.execute("CREATE TABLE drafts (payload TEXT, state TEXT, tenant TEXT)")
    try:
        yield db
    finally:
        db.close()


def report(root, profile, *, as_of=None, candidate=None, monthly_projection=None):
    as_of = as_of or models.today()
    if profile.issuer.tax_regime != "monotributo":
        return {"applicable": False, "reason": "El emisor no es monotributista"}
    path = root / "revenue-policy.json"
    if not path.exists():
        return {
            "applicable": True,
            "configured": False,
            "notice": "Falta categoría e historial: no se puede afirmar margen disponible",
        }
    saved = json.loads(path.read_text())
    if saved["issuer_cuit"] != profile.issuer.cuit:
        raise ValueError("El control de ingresos pertenece a otro CUIT")
    policy = RevenuePolicy.model_validate(saved["policy"])
    start = window_start(as_of)
    known = {}
    pending = Decimal(0)
    conflicts = []
    credits = Decimal(0)
    with read_journal(root) as db:
        rows = db.execute(
            "SELECT payload,state FROM drafts WHERE tenant LIKE ? AND state IN ('authorized','sending','uncertain')",
            (f"produccion:{profile.issuer.cuit}:%",),
        ).fetchall()
        for row in rows:
            summary = json.loads(row["payload"])
            inv = summary["invoice"]
            if (
                summary["environment"] != "produccion"
                or summary["issuer"]["cuit"] != profile.issuer.cuit
            ):
                raise ValueError("Historial con contribuyente inconsistente")
            when = date.fromisoformat(inv["invoice_date"])
            if start <= when <= as_of:
                if row["state"] != "authorized":
                    pending += Decimal(inv["total"])
                    continue
                key = f"{profile.issuer.cuit}:{inv['voucher_type']}:{summary['point_of_sale']}:{summary['number']}"
                known[key] = (inv["invoice_date"], inv["total"], "invoice")
        table = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='revenue_imports'"
        ).fetchone()
        for row in db.execute("SELECT data FROM revenue_imports").fetchall() if table else []:
            entry = RevenueEntry.model_validate_json(row[0])
            if entry.issuer_cuit != profile.issuer.cuit or not start <= entry.invoice_date <= as_of:
                continue
            value = (entry.invoice_date.isoformat(), entry.total, entry.kind)
            if entry.identity in known and known[entry.identity] != value:
                conflicts.append(entry.identity)
            known.setdefault(entry.identity, value)
    revenue = sum((Decimal(v[1]) for v in known.values() if v[2] != "credit"), Decimal(0))
    credits = sum((Decimal(v[1]) for v in known.values() if v[2] == "credit"), Decimal(0))
    extra = Decimal(candidate.total) if candidate is not None else Decimal(0)
    proposed = revenue + pending + extra
    official = LIMITS[policy.category]
    personal = Decimal(policy.personal_limit) if policy.personal_limit else official
    threshold = min(personal, official)
    warnings = []
    stale = not EFFECTIVE_FROM <= as_of <= REVIEW_BY or models.today() > REVIEW_BY
    covered = bool(
        policy.coverage_confirmed
        and policy.coverage_from <= start
        and policy.coverage_through >= as_of
    )
    if profile.issuer.activity_start > start:
        warnings.append(
            "Actividad con menos de 12 meses: revisar anualización y reglas de inicio con contador"
        )
    if stale:
        warnings.append(
            "Escala sin vigencia revisada para esta fecha; actualizar fuente antes de emitir"
        )
    if not covered:
        warnings.append("Historial incompleto o no confirmado: no acredita margen real")
    if conflicts:
        warnings.append("Hay comprobantes importados que difieren del diario; conciliar")
    if pending:
        warnings.append("Hay emisiones inciertas incluidas conservadoramente en la exposición")
    if credits:
        warnings.append(
            "Notas de crédito registradas sin descontar: revisar imputación con contador"
        )
    if proposed > LIMITS["K"]:
        warnings.append("Supera el máximo de ingresos del monotributo; revisar pase de régimen")
    elif proposed > official:
        warnings.append("Supera el tope de la categoría declarada; revisar recategorización")
    elif proposed >= official * Decimal(policy.warning_percent) / 100:
        warnings.append("Se aproxima al tope de la categoría declarada")
    if policy.personal_limit and proposed >= personal:
        warnings.append("Alcanza o supera el límite preventivo personal")
    result = {
        "applicable": True,
        "configured": True,
        "issuer_cuit": profile.issuer.cuit,
        "environment": profile.environment,
        "as_of": as_of.isoformat(),
        "window_from": start.isoformat(),
        "category": policy.category,
        "official_category_limit": str(official),
        "official_regime_limit": str(LIMITS["K"]),
        "personal_limit": policy.personal_limit,
        "known_gross_revenue": f"{revenue:.2f}",
        "uncertain_exposure": f"{pending:.2f}",
        "candidate_amount": f"{extra:.2f}",
        "projected_gross": f"{proposed:.2f}",
        "remaining_preventive_margin": f"{threshold - proposed:.2f}",
        "coverage_confirmed": covered,
        "credit_notes_not_deducted": f"{credits:.2f}",
        "conflicts": conflicts,
        "warnings": warnings,
        "source": SOURCE,
        "scale_effective_from": EFFECTIVE_FROM.isoformat(),
        "scale_verified_on": "2026-10-28",
        "scale_review_by": REVIEW_BY.isoformat(),
        "blocked": stale or not covered or bool(conflicts) or proposed > LIMITS["K"],
        "notice": "Control preventivo de ingresos por fecha de comprobante, no determinación fiscal. "
        "No controla alquileres, superficie, energía ni demás causales. No omitir ni diferir facturas para eludir límites.",
    }
    if monthly_projection is not None:
        monthly = Decimal(Invoice.money(monthly_projection))
        # Explicit steady-run-rate scenario; do not call this a moving-window forecast.
        result["run_rate_scenario"] = {
            "monthly_amount": str(monthly),
            "annualized_amount": f"{monthly * 12:.2f}",
            "exceeds_category_limit": monthly * 12 > official,
            "exceeds_regime_limit": monthly * 12 > LIMITS["K"],
            "assumption": "12 meses con ese importe fijo; no predice cobros ni reemplaza el acumulado móvil",
        }
    result["confirmation"] = "REVISAR INGRESOS " + digest(result)
    return result


def check_issue(root, profile, inv, confirmation):
    if profile.environment != "produccion":
        return
    current = report(root, profile, candidate=inv)
    if not current.get("configured"):
        return  # Existing installations remain usable; status explicitly reports no control.
    if current["blocked"]:
        raise ValueError("Control de ingresos bloqueado: " + "; ".join(current["warnings"]))
    if current["warnings"] and confirmation != current["confirmation"]:
        raise ValueError(
            "Revisá revenue_status con esta factura y confirmá sus advertencias antes de emitir"
        )
