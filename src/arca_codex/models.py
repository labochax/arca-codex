from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def today() -> date:
    return datetime.now(TZ).date()


def cuit_valid(value: str) -> str:
    if not re.fullmatch(r"[0-9]{11}", value):
        raise ValueError("CUIT: ingresar 11 dígitos, sin guiones")
    check = 11 - sum(int(x) * w for x, w in zip(value[:10], [5, 4, 3, 2, 7, 6, 5, 4, 3, 2])) % 11
    check = 0 if check == 11 else 9 if check == 10 else check
    if check != int(value[-1]):
        raise ValueError("CUIT: dígito verificador inválido")
    return value


def cbu_valid(value: str) -> str:
    if not re.fullmatch(r"[0-9]{22}", value):
        raise ValueError("CBU: debe contener 22 dígitos")
    for body, check, weights in [
        (value[:7], value[7], [7, 1, 3, 9, 7, 1, 3]),
        (value[8:21], value[21], [3, 9, 7, 1, 3, 9, 7, 1, 3, 9, 7, 1, 3]),
    ]:
        if (10 - sum(int(x) * w for x, w in zip(body, weights)) % 10) % 10 != int(check):
            raise ValueError("CBU: dígito verificador inválido")
    return value


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Issuer(StrictModel):
    cuit: str
    name: str = Field(min_length=1, max_length=120)
    address: str = Field(min_length=1, max_length=240)
    gross_income_registration: str = Field(min_length=1, max_length=80)
    activity_start: date
    _cuit = field_validator("cuit")(cuit_valid)


class Profile(StrictModel):
    environment: Literal["homologacion", "produccion"] = "homologacion"
    issuer: Issuer
    point_of_sale: int = Field(ge=1, le=99999)
    production_enabled: bool = False
    certificate_path: str
    encrypted_key_path: str
    keyring_account: str


class Invoice(StrictModel):
    """Deliberately narrow: one service line, ARS, identified domestic recipient."""

    voucher_type: Literal[11, 211]
    recipient_cuit: str
    recipient_name: str = Field(min_length=1, max_length=120)
    recipient_address: str = Field(min_length=1, max_length=240)
    recipient_iva_id: int = Field(ge=1, le=99)
    recipient_iva_label: str = Field(min_length=1, max_length=100)
    total: str
    invoice_date: date
    service_from: date
    service_to: date
    due_date: date
    description: str = Field(min_length=1, max_length=700)
    sale_condition: str = Field(min_length=1, max_length=100)
    payment_received_on: date | None = None
    commercial_reference: str | None = Field(default=None, min_length=1, max_length=50)
    cbu: str | None = None
    cbu_alias: str | None = Field(default=None, pattern=r"^[A-Za-z0-9.\-]{6,20}$")
    transfer: Literal["SCA", "ADC"] | None = None

    _cuit = field_validator("recipient_cuit")(cuit_valid)

    @field_validator("total")
    @classmethod
    def money(cls, value: str) -> str:
        if not re.fullmatch(r"[0-9]{1,13}(\.[0-9]{1,2})?", value):
            raise ValueError('Importe: texto decimal sin miles, por ejemplo "1234.56"')
        if Decimal(value) <= 0:
            raise ValueError("Importe debe ser positivo")
        return f"{Decimal(value):.2f}"

    @model_validator(mode="after")
    def rules(self):
        if self.service_to < self.service_from:
            raise ValueError("El período de servicio está invertido")
        if self.due_date < self.invoice_date:
            raise ValueError("El vencimiento no puede ser anterior a la emisión")
        if self.voucher_type == 211:
            if not self.cbu or not self.transfer:
                raise ValueError("FCE C requiere CBU y transferencia SCA o ADC")
            cbu_valid(self.cbu)
        elif any([self.cbu, self.cbu_alias, self.transfer]):
            raise ValueError("Los opcionales FCE no corresponden a Factura C común")
        return self

    def validate_issue_date(self, current_date: date | None = None):
        delta = (self.invoice_date - (current_date or today())).days
        # WSFEv1 v4.7, CbteFch. FCE overrides the ordinary services window.
        low, high = (-5, 1) if self.voucher_type == 211 else (-10, 10)
        if not low <= delta <= high:
            raise ValueError(
                f"Fecha fuera de la ventana ARCA ({low} a +{high} días). "
                "No confundir fecha de cobro con fecha de emisión."
            )

    def detail(self, number: int) -> dict:
        d = dict(
            Concepto=2,
            DocTipo=80,
            DocNro=int(self.recipient_cuit),
            CbteDesde=number,
            CbteHasta=number,
            CbteFch=self.invoice_date.strftime("%Y%m%d"),
            ImpTotal=self.total,
            ImpTotConc="0.00",
            ImpNeto=self.total,
            ImpOpEx="0.00",
            ImpTrib="0.00",
            ImpIVA="0.00",
            FchServDesde=self.service_from.strftime("%Y%m%d"),
            FchServHasta=self.service_to.strftime("%Y%m%d"),
            FchVtoPago=self.due_date.strftime("%Y%m%d"),
            MonId="PES",
            MonCotiz="1",
            CondicionIVAReceptorId=self.recipient_iva_id,
        )
        opts = []
        if self.voucher_type == 211:
            opts += [{"Id": "2101", "Valor": self.cbu}, {"Id": "27", "Valor": self.transfer}]
            if self.cbu_alias:
                opts.append({"Id": "2102", "Valor": self.cbu_alias})
        if self.commercial_reference:
            opts.append({"Id": "23", "Valor": self.commercial_reference})
        if opts:
            d["Opcionales"] = {"Opcional": opts}
        return d


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
