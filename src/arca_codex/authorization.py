"""Bind a persisted authorization to its exact draft, request and reply (no secrets)."""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from .models import Invoice, digest
from .soap import many


def _equal(key, actual, expected):
    if key == "Iva":

        def rates(value):
            return {
                int(x["Id"]): (Decimal(str(x["BaseImp"])), Decimal(str(x["Importe"])))
                for x in many((value or {}).get("AlicIva"))
            }

        return rates(actual) == rates(expected)
    if key == "Opcionales":

        def options(value):
            return {str(x["Id"]): str(x["Valor"]) for x in many(value.get("Opcional"))}

        return options(actual or {}) == options(expected)
    if key.startswith("Imp") or key == "MonCotiz":
        return Decimal(str(actual)) == Decimal(str(expected))
    return str(actual) == str(expected)


def _check(actual, expected, keys):
    for key in keys:
        if key not in actual or not _equal(key, actual[key], expected[key]):
            raise ValueError(f"Autorización difiere del borrador en {key}")


def make_authorization(summary, reply, *, consulted=False):
    """Call only after receiving a reply; never generates or requests a CAE."""
    inv = Invoice.model_validate(summary["invoice"])
    expected = inv.detail(summary["number"])
    if consulted:
        expected.update(PtoVta=summary["point_of_sale"], CbteTipo=inv.voucher_type)
        _check(reply, expected, expected)
        if reply.get("Resultado") != "A" or reply.get("EmisionTipo") != "CAE":
            raise ValueError("La consulta no devuelve una autorización CAE")
        cae, expiry = str(reply.get("CodAutorizacion", "")), reply.get("FchVto", "")
        evidence = {
            k: reply[k]
            for k in [*expected, "Resultado", "EmisionTipo", "CodAutorizacion", "FchVto"]
        }
    else:
        header = reply.get("FeCabResp") or {}
        _check(
            header,
            {
                "Cuit": int(summary["issuer"]["cuit"]),
                "PtoVta": summary["point_of_sale"],
                "CbteTipo": inv.voucher_type,
                "CantReg": 1,
                "Resultado": "A",
            },
            ("Cuit", "PtoVta", "CbteTipo", "CantReg", "Resultado"),
        )
        details = many(reply.get("FeDetResp", {}).get("FECAEDetResponse"))
        if len(details) != 1:
            raise ValueError("Autorización sin un único detalle")
        detail = details[0]
        _check(
            detail, expected, ("Concepto", "DocTipo", "DocNro", "CbteDesde", "CbteHasta", "CbteFch")
        )
        _check(detail, expected, set(detail) & set(expected))
        if detail.get("Resultado") != "A":
            raise ValueError("Detalle no autorizado")
        cae, expiry = str(detail.get("CAE", "")), detail.get("CAEFchVto", "")
        # Only keep known fiscal response fields, never request XML or authentication.
        evidence = {
            "FeCabResp": {
                k: header[k] for k in ("Cuit", "PtoVta", "CbteTipo", "CantReg", "Resultado")
            },
            "FeDetResp": {
                "FECAEDetResponse": {
                    k: v
                    for k, v in detail.items()
                    if k in set(expected) | {"Resultado", "CAE", "CAEFchVto"}
                }
            },
        }
    if not re.fullmatch(r"[0-9]{14}", cae) or cae.startswith("0"):
        raise ValueError("CAE inválido")
    due = datetime.strptime(expiry, "%Y%m%d").date()
    if due < date.fromisoformat(summary["invoice"]["invoice_date"]):
        raise ValueError("Vencimiento de CAE anterior a la emisión")
    return {
        "cae": cae,
        "cae_due": due.isoformat(),
        "binding": {"summary_hash": digest(summary), "consulted": consulted, "response": evidence},
    }


def validate_authorization(summary, result):
    binding = result.get("binding") or {}
    if binding.get("summary_hash") != digest(summary):
        raise ValueError("Autorización sin vínculo verificable con el borrador; revisar historial")
    rebuilt = make_authorization(summary, binding["response"], consulted=binding["consulted"])
    if result["cae"] != rebuilt["cae"] or result["cae_due"] != rebuilt["cae_due"]:
        raise ValueError("CAE o vencimiento no coincide con la respuesta persistida")
