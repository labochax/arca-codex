"""Local representation rules. Never reads credentials or contacts ARCA."""

from __future__ import annotations

from datetime import date
from decimal import Decimal


def acceptance_days(invoice_date: str) -> int:
    # Resolution 219/2025, art. 1. Do not silently extend an expired exception.
    if date(2025, 11, 1) <= date.fromisoformat(invoice_date) <= date(2026, 10, 31):
        return 21
    raise ValueError("Revisar normativa FCE: fecha fuera de la vigencia verificada (219/2025)")


def acceptance_legend(invoice_date: str) -> str:
    days = acceptance_days(invoice_date)
    return (
        f"Esta Factura de Crédito Electrónica MiPyMEs se considerará aceptada si, al vencer "
        f"el plazo de {days} días corridos desde su recepción en el domicilio fiscal electrónico "
        "del comprador o locatario, no se hubiera registrado su rechazo total o su aceptación. "
        "Si al vencimiento de ese plazo no se hubiera registrado la cancelación, constituirá "
        "título ejecutivo en los términos del artículo 523 del Código Procesal Civil y Comercial "
        "de la Nación y concordantes. La aceptación expresa o tácita implica plena conformidad "
        "para transferir la información del documento desde el Registro de Facturas Electrónicas "
        "MiPyMEs a terceros si el vendedor o locador opta por su cesión, transmisión o negociación."
    )


def circulation_legend(transfer: str) -> str:
    destinations = {
        "SCA": "el Sistema de Circulación Abierta para Facturas de Crédito Electrónicas MiPyMEs, "
        "para su circulación y negociación, incluso en los Mercados de Valores, en este caso, "
        "a través de un Agente de Depósito Colectivo o agentes que cumplan similares funciones",
        "ADC": "un Agente de Depósito Colectivo o agentes que cumplan similares funciones, "
        "para su negociación en los Mercados de Valores",
    }
    return (
        "Luego de su aceptación tácita o expresa, esta Factura de Crédito Electrónica MiPyMEs "
        "será transmitida a " + destinations[transfer] + "."
    )


def _under_thousand(n: int) -> str:
    small = (
        "cero",
        "uno",
        "dos",
        "tres",
        "cuatro",
        "cinco",
        "seis",
        "siete",
        "ocho",
        "nueve",
        "diez",
        "once",
        "doce",
        "trece",
        "catorce",
        "quince",
        "dieciséis",
        "diecisiete",
        "dieciocho",
        "diecinueve",
        "veinte",
        "veintiuno",
        "veintidós",
        "veintitrés",
        "veinticuatro",
        "veinticinco",
        "veintiséis",
        "veintisiete",
        "veintiocho",
        "veintinueve",
    )
    if n < 30:
        return small[n]
    if n < 100:
        tens = {
            3: "treinta",
            4: "cuarenta",
            5: "cincuenta",
            6: "sesenta",
            7: "setenta",
            8: "ochenta",
            9: "noventa",
        }
        return tens[n // 10] + (" y " + small[n % 10] if n % 10 else "")
    if n == 100:
        return "cien"
    hundreds = {
        1: "ciento",
        2: "doscientos",
        3: "trescientos",
        4: "cuatrocientos",
        5: "quinientos",
        6: "seiscientos",
        7: "setecientos",
        8: "ochocientos",
        9: "novecientos",
    }
    return hundreds[n // 100] + (" " + _under_thousand(n % 100) if n % 100 else "")


def _apocope(words: str) -> str:
    if words.endswith("veintiuno"):
        return words[:-9] + "veintiún"
    if words.endswith("uno"):
        return words[:-3] + "un"
    return words


def _integer_words(n: int) -> str:
    if n < 1000:
        return _under_thousand(n)
    for divisor, singular, plural in (
        (10**12, "un billón", "billones"),
        (10**6, "un millón", "millones"),
        (1000, "mil", "mil"),
    ):
        if n >= divisor:
            q, r = divmod(n, divisor)
            head = singular if q == 1 else _apocope(_integer_words(q)) + " " + plural
            return head + (" " + _integer_words(r) if r else "")
    raise AssertionError("unreachable")


def amount_in_words(value: str) -> str:
    total = Decimal(value)
    if (
        not total.is_finite()
        or total < 0
        or total >= 10**13
        or total != total.quantize(Decimal(".01"))
    ):
        raise ValueError("Importe fuera de rango o con más de dos decimales")
    units = int(total)
    cents = int((total - units) * 100)
    noun = "peso" if units == 1 else "pesos"
    connector = " de" if units and units % 10**6 == 0 else ""
    return f"{_apocope(_integer_words(units))}{connector} {noun} con {cents:02d}/100".upper()
