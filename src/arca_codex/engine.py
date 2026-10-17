from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal

from filelock import FileLock

from .models import Invoice, canonical, digest
from .soap import many
from .storage import database, decode_row


class Engine:
    def __init__(self, profile, root, client):
        self.profile, self.root, self.client = profile, root, client
        self.tenant = f"{profile.environment}:{profile.issuer.cuit}:{profile.point_of_sale}"

    def lock(self):
        return FileLock(str(self.root / "issuance.lock"), timeout=45)

    def preview(self, data):
        inv = Invoice.model_validate(data)
        inv.validate_issue_date()
        return {
            "environment": self.profile.environment,
            "issuer": self.profile.issuer.model_dump(mode="json"),
            "point_of_sale": self.profile.point_of_sale,
            "invoice": inv.model_dump(mode="json"),
            "notice": "Borrador sin CAE. El detalle de servicios se conserva localmente; "
            "WSFEv1 recibe importes y datos fiscales, no la descripción de ítems.",
        }

    def prepare(self, data, request_key):
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", request_key):
            raise ValueError("request_key debe ser estable y contener 8 a 80 letras/dígitos/_/-")
        payload = self.preview(data)
        inv_data = payload["invoice"]
        fp = digest(
            {
                k: inv_data[k]
                for k in ("voucher_type", "recipient_cuit", "total", "service_from", "service_to")
            }
        )
        with self.lock(), database(self.root) as db:
            rows = db.execute(
                "SELECT * FROM drafts WHERE tenant=? AND (request_key=? OR "
                "(fingerprint=? AND state NOT IN ('rejected','stale'))) ORDER BY created_at DESC",
                (self.tenant, request_key, fp),
            ).fetchall()
            if rows:
                row = decode_row(rows[0])
                old = {
                    k: v for k, v in row["payload"].items() if k not in ("number", "request_key")
                }
                if old != payload:
                    raise ValueError("La clave de idempotencia ya corresponde a otros datos")
                return self.public(row)
            pending = db.execute(
                "SELECT id FROM drafts WHERE tenant=? AND state IN ('sending','uncertain')",
                (self.tenant,),
            ).fetchone()
            if pending:
                raise ValueError(f"Hay una emisión pendiente de conciliación: {pending['id']}")
            points = self.client.points()
            p = next((p for p in points if int(p["Nro"]) == self.profile.point_of_sale), None)
            if (
                not p
                or p.get("Bloqueado") != "N"
                or p.get("FchBaja") not in (None, "", "NULL", "00000000")
            ):
                raise ValueError("Punto de venta ausente, bloqueado o dado de baja en WSFE")
            conditions = self.client.iva_conditions()
            inv = Invoice.model_validate(payload["invoice"])
            condition = next((c for c in conditions if int(c["Id"]) == inv.recipient_iva_id), None)
            if not condition:
                raise ValueError("Condición IVA no habilitada por ARCA para clase C")
            # Names in PDF must agree with the official parameter table.
            if (
                condition.get("Desc", "").casefold().strip()
                != inv.recipient_iva_label.casefold().strip()
            ):
                raise ValueError(
                    f"Usá la descripción oficial para esa condición IVA: {condition.get('Desc')}"
                )
            number = self.client.last(inv.voucher_type) + 1
            payload["number"] = number
            payload["request_key"] = request_key
            identity = digest({"tenant": self.tenant, "payload": payload})
            now = datetime.now(UTC).isoformat()
            db.execute(
                "INSERT INTO drafts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    self.tenant,
                    request_key,
                    fp,
                    canonical(payload),
                    "prepared",
                    number,
                    None,
                    now,
                ),
            )
            db.commit()
            return self.public(
                decode_row(db.execute("SELECT * FROM drafts WHERE id=?", (identity,)).fetchone())
            )

    def get(self, draft_id):
        with database(self.root) as db:
            row = decode_row(
                db.execute(
                    "SELECT * FROM drafts WHERE id=? AND tenant=?", (draft_id, self.tenant)
                ).fetchone()
            )
        if (
            digest({"tenant": self.tenant, "payload": row["payload"]}) != draft_id
            or row["number"] != row["payload"]["number"]
        ):
            raise ValueError("El borrador no coincide con su hash; no se emitirá")
        return row

    def public(self, row):
        return {
            "draft_id": row["id"],
            "state": row["state"],
            "summary": row["payload"],
            "confirmation": f"EMITIR {row['id']}",
            "result": row["result"],
        }

    def set_state(self, draft_id, state, result=None):
        with database(self.root) as db:
            db.execute(
                "UPDATE drafts SET state=?, result=? WHERE id=? AND tenant=?",
                (state, canonical(result) if result else None, draft_id, self.tenant),
            )
            db.commit()

    def emit(self, draft_id, confirmation):
        if confirmation != f"EMITIR {draft_id}":
            raise ValueError("Confirmación exacta requerida para este borrador")
        if self.profile.environment == "produccion" and not self.profile.production_enabled:
            raise ValueError(
                "Producción deshabilitada. Habilitala localmente después de homologación."
            )
        with self.lock():
            row = self.get(draft_id)
            if row["state"] == "authorized":
                return self.public(row)
            if row["state"] != "prepared":
                raise ValueError("Este borrador no se puede enviar; consultá su estado o conciliá")
            with database(self.root) as db:
                pending = db.execute(
                    "SELECT id FROM drafts WHERE tenant=? AND state IN ('sending','uncertain')",
                    (self.tenant,),
                ).fetchone()
            if pending:
                raise ValueError("Hay una operación incierta; conciliá antes de emitir")
            # Bind approval to issuer metadata, environment and POS as well as invoice.
            if row["payload"]["issuer"] != self.profile.issuer.model_dump(mode="json"):
                raise ValueError("Cambió el perfil del emisor; prepará un nuevo borrador")
            inv = Invoice.model_validate(row["payload"]["invoice"])
            inv.validate_issue_date()
            if self.client.last(inv.voucher_type) + 1 != row["number"]:
                self.set_state(draft_id, "stale")
                raise ValueError("Cambió la numeración de ARCA; revisar y preparar con otra clave")
            self.set_state(draft_id, "sending")  # durable BEFORE the network write
            try:
                reply = self.client.authorize(inv.voucher_type, inv.detail(row["number"]))
                header = reply.get("FeCabResp") or {}
                if (
                    int(header.get("PtoVta", -1)) != self.profile.point_of_sale
                    or int(header.get("CbteTipo", -1)) != inv.voucher_type
                    or int(header.get("CantReg", -1)) != 1
                ):
                    raise ValueError("Cabecera devuelta no coincide con el comprobante")
                details = many(reply.get("FeDetResp", {}).get("FECAEDetResponse"))
                if len(details) != 1:
                    raise ValueError("Respuesta de autorización incompleta")
                d = details[0]
                if (
                    int(d.get("CbteDesde", -1)) != row["number"]
                    or int(d.get("CbteHasta", -1)) != row["number"]
                ):
                    raise ValueError("Numeración devuelta no coincide")
                if d.get("Resultado") == "R":
                    codes = [
                        str(x.get("Code")) for x in many(d.get("Observaciones", {}).get("Obs"))
                    ]
                    self.set_state(draft_id, "rejected", {"codes": codes})
                elif d.get("Resultado") == "A" and re.fullmatch(
                    r"[0-9]{14}", str(d.get("CAE", ""))
                ):
                    expiry = datetime.strptime(d["CAEFchVto"], "%Y%m%d").date().isoformat()
                    self.set_state(
                        draft_id,
                        "authorized",
                        {
                            "cae": d["CAE"],
                            "cae_due": expiry,
                            "observation_codes": [
                                str(x.get("Code"))
                                for x in many(d.get("Observaciones", {}).get("Obs"))
                            ],
                        },
                    )
                else:
                    raise ValueError("Respuesta no concluyente")
            except Exception:
                self.set_state(draft_id, "uncertain", {"action": "reconcile_invoice"})
                # Do not leak authentication errors, request XML, or response payloads.
                return self.public(self.get(draft_id))
            return self.public(self.get(draft_id))

    def reconcile(self, draft_id):
        with self.lock():
            row = self.get(draft_id)
            if row["state"] == "authorized":
                return self.public(row)
            if row["state"] not in ("sending", "uncertain"):
                raise ValueError("Solo se concilian emisiones enviadas o inciertas")
            inv = Invoice.model_validate(row["payload"]["invoice"])
            found = self.client.consult(inv.voucher_type, row["number"])
            if not found:
                # Even 'not found' is not permission to resubmit after an ambiguous write.
                return {
                    **self.public(row),
                    "notice": "ARCA aún no devuelve ese comprobante. "
                    "Permanece bloqueado; no reenviar ni cambiar de número. Revisar manualmente.",
                }
            expected = inv.detail(row["number"])
            expected.update(PtoVta=self.profile.point_of_sale, CbteTipo=inv.voucher_type)
            for key, value in expected.items():
                if key == "Opcionales":
                    opts = found.get("Opcionales") or {}
                    actual = {str(o["Id"]): str(o["Valor"]) for o in many(opts.get("Opcional"))}
                    wanted = {str(o["Id"]): str(o["Valor"]) for o in value["Opcional"]}
                    match = actual == wanted
                elif key.startswith("Imp") or key == "MonCotiz":
                    match = key in found and Decimal(str(found[key])) == Decimal(str(value))
                else:
                    match = str(found.get(key)) == str(value)
                if not match:
                    raise ValueError(
                        f"Comprobante remoto difiere en {key}; requiere revisión manual"
                    )
            cae = str(found.get("CodAutorizacion", ""))
            if (
                found.get("Resultado") != "A"
                or found.get("EmisionTipo") != "CAE"
                or not re.fullmatch(r"[0-9]{14}", cae)
            ):
                raise ValueError("No se encontró una autorización CAE válida")
            expiry = datetime.strptime(found["FchVto"], "%Y%m%d").date().isoformat()
            self.set_state(
                draft_id, "authorized", {"cae": cae, "cae_due": expiry, "reconciled": True}
            )
            return self.public(self.get(draft_id))
