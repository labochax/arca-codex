from datetime import date

import pytest

from arca_codex.models import Profile


@pytest.fixture(autouse=True)
def fixed_date(monkeypatch):
    monkeypatch.setattr("arca_codex.models.today", lambda: date(2026, 10, 16))


@pytest.fixture
def profile():
    return Profile(
        environment="homologacion",
        point_of_sale=2,
        issuer={
            "cuit": "20123456786",
            "name": "EMISOR DE PRUEBA",
            "address": "Domicilio ficticio 123",
            "gross_income_registration": "Exento",
            "activity_start": "2024-01-01",
        },
        certificate_path="unused",
        encrypted_key_path="unused",
        keyring_account="test",
    )


@pytest.fixture
def invoice():
    return dict(
        voucher_type=211,
        recipient_cuit="30712345671",
        recipient_name="CLIENTE DE PRUEBA SA",
        recipient_address="Domicilio ficticio 456",
        recipient_iva_id=1,
        recipient_iva_label="IVA Responsable Inscripto",
        total="123456.78",
        invoice_date="2026-10-16",
        service_from="2026-09-01",
        service_to="2026-09-30",
        due_date="2026-10-16",
        description="Asesoría - septiembre",
        sale_condition="Transferencia bancaria",
        payment_received_on="2026-10-09",
        commercial_reference="DEMO-SEP-26",
        cbu="0000000000000000000000",
        transfer="SCA",
    )


class FakeArca:
    def __init__(self):
        self.number = 7
        self.calls = 0
        self.fail = False
        self.reject = False
        self.remote = None

    def points(self):
        return [{"Nro": "2", "EmisionTipo": "CAE", "Bloqueado": "N", "FchBaja": "NULL"}]

    def iva_conditions(self):
        return [{"Id": "1", "Desc": "IVA Responsable Inscripto"}]

    def last(self, kind):
        return self.number

    def authorize(self, kind, detail):
        self.calls += 1
        self.remote = {
            **detail,
            "Resultado": "A",
            "CodAutorizacion": "12345678901234",
            "EmisionTipo": "CAE",
            "FchVto": "20261016",
            "PtoVta": "2",
            "CbteTipo": str(kind),
        }
        self.number = detail["CbteDesde"]
        if self.fail:
            raise TimeoutError("simulated response lost AFTER authorization")
        return {
            "FeCabResp": {
                "Cuit": "20123456786",
                "PtoVta": "2",
                "CbteTipo": str(kind),
                "CantReg": "1",
                "Resultado": "R" if self.reject else "A",
            },
            "FeDetResp": {
                "FECAEDetResponse": {
                    "Concepto": detail["Concepto"],
                    "DocTipo": detail["DocTipo"],
                    "DocNro": detail["DocNro"],
                    "CbteFch": detail["CbteFch"],
                    "CbteDesde": detail["CbteDesde"],
                    "CbteHasta": detail["CbteHasta"],
                    "Resultado": "R" if self.reject else "A",
                    "CAE": "12345678901234",
                    "CAEFchVto": "20261016",
                }
            },
        }

    def consult(self, kind, number):
        return self.remote


@pytest.fixture
def arca():
    return FakeArca()
