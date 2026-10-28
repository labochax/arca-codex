from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .credentials import credential_account, generate, import_pair, load_pair
from .models import Issuer, Profile, cuit_valid
from .storage import data_home, load_profile, write_private


def ask(label, default=None):
    value = input(label + (f" [{default}]" if default is not None else "") + ": ").strip()
    return value or default


def save_profile(root, profile):
    write_private(root / "profile.json", profile.model_dump_json(indent=2).encode())


def setup(root, *, existing=False):
    if (root / "profile.json").exists():
        raise ValueError(
            "Ya existe profile.json. Respaldalo y usá otra carpeta privada para otro perfil."
        )
    print(
        "Configuración local. No ingreses tu clave fiscal. Homologación y producción usan certificados distintos."
    )
    env = ask("Ambiente (homologacion/produccion)", "homologacion")
    if env not in ("homologacion", "produccion"):
        raise ValueError("Ambiente inválido")
    cuit = cuit_valid(ask("CUIT emisor, sin guiones"))
    name = ask("Nombre / razón social del emisor")
    certificate_cuit = cuit_valid(
        ask("CUIT titular del certificado (propio o representante autorizado)", cuit)
    )
    delegated = certificate_cuit != cuit
    if (
        delegated
        and ask(
            "¿La delegación WSFE del emisor al representante está autorizada en ARCA? (SI/NO)", "NO"
        )
        != "SI"
    ):
        raise ValueError("Primero completar delegación WSFE en ARCA; no compartir clave fiscal")
    folder = root / "credentials" / env / certificate_cuit
    if existing and not (folder / "certificate.pem").exists():
        print(
            "Importación local. Los archivos originales no se modifican. No pegues claves por chat."
        )
        cert_source = Path(ask("Ruta del certificado PEM/CRT existente"))
        key_source = Path(ask("Ruta de la clave privada existente"))
        secret = getpass.getpass("Contraseña de esa clave, solo aquí (vacío si no tiene): ")
        import_pair(root, certificate_cuit, env, cert_source, key_source, secret)
    if not (folder / "request.csr").exists() and not (folder / "certificate.pem").exists():
        paths = generate(root, certificate_cuit, env, name)
        print("Solicitud CSR creada: " + paths["csr_path"])
        print(
            "Subí SOLO ese CSR en ARCA, descargá el certificado y volvé a ejecutar setup. "
            "La clave privada queda cifrada localmente; su contraseña está en el llavero del sistema."
        )
        return
    regime = ask("Régimen (monotributo/responsable_inscripto/exento)", "monotributo")
    issuer = Issuer(
        cuit=cuit,
        tax_regime=regime,
        name=name,
        address=ask("Domicilio comercial"),
        gross_income_registration=ask("Ingresos Brutos (número o condición)"),
        activity_start=ask("Inicio de actividades (AAAA-MM-DD)"),
    )
    point = int(ask("Punto de venta habilitado para Web Services (NO Comprobantes en Línea)"))
    source = (
        Path(
            ask(
                "Ruta del certificado .crt/.pem descargado de ARCA",
                str(folder / "certificate.pem") if (folder / "certificate.pem").exists() else None,
            )
        )
        .expanduser()
        .resolve()
    )
    target = folder / "certificate.pem"
    if source != target:
        write_private(target, source.read_bytes())
    profile = Profile(
        environment=env,
        issuer=issuer,
        point_of_sale=point,
        certificate_path=str(target),
        encrypted_key_path=str(folder / "private-key.pem"),
        keyring_account=credential_account(root, env, certificate_cuit),
        certificate_cuit=certificate_cuit,
        delegation_confirmed=delegated,
        ordinary_a_authorized=(
            regime == "responsable_inscripto"
            and ask("¿ARCA habilita A común sin leyendas especiales? (SI/NO)", "NO") == "SI"
        ),
        production_enabled=False,
    )
    load_pair(profile)  # validate pairing, encryption and certificate validity before accepting
    save_profile(root, profile)
    print("Configuración guardada. Ejecutá arca-codex doctor para comprobar acceso a WSFE.")


def main():
    parser = argparse.ArgumentParser(description="Facturación local ARCA para Codex")
    parser.add_argument(
        "--profile", help="Perfil privado separado (no cambia otras conexiones MCP)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    setup_parser = sub.add_parser("setup", help="Asistente local de credenciales y perfil")
    setup_parser.add_argument(
        "--existing", action="store_true", help="Importar certificado y clave existentes localmente"
    )
    sub.add_parser("doctor", help="Validar certificado y consultar puntos de venta sin emitir")
    sub.add_parser("enable-production", help="Habilitación local explícita tras homologación")
    sub.add_parser("serve", help="Iniciar servidor MCP stdio")
    sub.add_parser("profiles", help="Listar perfiles locales sin secretos")
    sub.add_parser("revenue", help="Mostrar control preventivo de ingresos")
    sub.add_parser("paths", help="Mostrar carpeta local privada, nunca secretos")
    args = parser.parse_args()
    try:
        if args.profile:
            os.environ["ARCA_CODEX_PROFILE"] = args.profile
        if args.command == "serve":
            from .server import main as serve

            serve()
            return
        root = data_home()
        if args.command == "profiles":
            from .profiles import list_profiles

            print(json.dumps(list_profiles(), ensure_ascii=False, indent=2))
        elif args.command == "revenue":
            from .monitor import report

            print(json.dumps(report(root, load_profile(root)), ensure_ascii=False, indent=2))
        elif args.command == "paths":
            print(root)
        elif args.command == "setup":
            setup(root, existing=args.existing)
        elif args.command == "doctor":
            from .soap import Wsfe

            p = load_profile(root)
            _, cert = load_pair(p)
            client = Wsfe(p, root)
            points = client.points()
            catalog_missing_in_homologation = not points and p.environment == "homologacion"
            configured_point = next(
                (point for point in points if int(point["Nro"]) == p.point_of_sale), None
            )
            if not catalog_missing_in_homologation and (
                not configured_point
                or configured_point.get("Bloqueado") != "N"
                or configured_point.get("FchBaja") not in (None, "", "NULL", "00000000")
            ):
                raise ValueError("Punto de venta ausente, bloqueado o dado de baja para el emisor")
            fallback_type = (
                1
                if p.issuer.tax_regime == "responsable_inscripto"
                else 11
                if p.issuer.tax_regime == "exento"
                else 211
            )
            fallback_number = (
                client.last(fallback_type) if catalog_missing_in_homologation else None
            )
            print(
                json.dumps(
                    {
                        "environment": p.environment,
                        "certificate_expires": cert.not_valid_after_utc.isoformat(),
                        "health": client.health(),
                        "points_of_sale": points,
                        "points_catalog_available": bool(points),
                        "configured_point_of_sale": p.point_of_sale,
                        "last_fce_number": fallback_number if fallback_type == 211 else None,
                        "last_checked_voucher_type": fallback_type
                        if catalog_missing_in_homologation
                        else None,
                        "last_checked_number": fallback_number,
                        "notice": (
                            f"Homologación sin catálogo de puntos: se consultó el último número del tipo {fallback_type}. "
                            "Esto no acredita habilitación ni autoriza una factura."
                            if catalog_missing_in_homologation
                            else None
                        ),
                        "tax_regime": p.issuer.tax_regime,
                        "issuer_cuit": p.issuer.cuit,
                        "certificate_cuit": p.certificate_cuit or p.issuer.cuit,
                        "iva_conditions": client.iva_conditions(
                            "A" if p.issuer.tax_regime == "responsable_inscripto" else "C"
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "enable-production":
            p = load_profile(root)
            if p.environment != "produccion":
                raise ValueError("El perfil no es de producción")
            load_pair(p)
            print(
                "Esto habilita facturas con efectos fiscales reales. Antes debés completar "
                "una emisión y consulta en homologación y revisar su PDF."
            )
            phrase = f"HABILITAR PRODUCCION {p.issuer.cuit}"
            if input(f"Si completaste esos pasos, escribí {phrase}: ") != phrase:
                raise ValueError("No se confirmó la habilitación")
            save_profile(root, p.model_copy(update={"production_enabled": True}))
            print("Producción habilitada. Cada factura sigue requiriendo revisión y confirmación.")
    except Exception as exc:
        # CLI handles configuration errors; do not emit tracebacks containing request state.
        from pydantic import ValidationError

        from .soap import ArcaError

        if isinstance(exc, ValidationError):
            message = "; ".join(
                ".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors()
            )
        elif isinstance(exc, (ValueError, ArcaError)):
            message = str(exc)
        else:
            message = (
                "No se pudo completar la operación. Revisá configuración, llavero y conectividad."
            )
        print("Error: " + message, file=sys.stderr)
        raise SystemExit(1)
