from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .credentials import generate, load_pair
from .models import Issuer, Profile, cuit_valid
from .storage import data_home, load_profile, write_private


def ask(label, default=None):
    value = input(label + (f" [{default}]" if default is not None else "") + ": ").strip()
    return value or default


def save_profile(root, profile):
    write_private(root / "profile.json", profile.model_dump_json(indent=2).encode())


def setup(root):
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
    folder = root / "credentials" / env / cuit
    if not (folder / "request.csr").exists():
        paths = generate(root, cuit, env, name)
        print("Solicitud CSR creada: " + paths["csr_path"])
        print(
            "Subí SOLO ese CSR en ARCA, descargá el certificado y volvé a ejecutar setup. "
            "La clave privada queda cifrada localmente; su contraseña está en el llavero del sistema."
        )
        return
    issuer = Issuer(
        cuit=cuit,
        name=name,
        address=ask("Domicilio comercial"),
        gross_income_registration=ask("Ingresos Brutos (número o condición)"),
        activity_start=ask("Inicio de actividades (AAAA-MM-DD)"),
    )
    point = int(ask("Punto de venta habilitado para Web Services (NO Comprobantes en Línea)"))
    source = Path(ask("Ruta del certificado .crt/.pem descargado de ARCA")).expanduser().resolve()
    target = folder / "certificate.pem"
    if source != target:
        write_private(target, source.read_bytes())
    profile = Profile(
        environment=env,
        issuer=issuer,
        point_of_sale=point,
        certificate_path=str(target),
        encrypted_key_path=str(folder / "private-key.pem"),
        keyring_account=f"{env}:{cuit}",
        production_enabled=False,
    )
    load_pair(profile)  # validate pairing, encryption and certificate validity before accepting
    save_profile(root, profile)
    print("Configuración guardada. Ejecutá arca-codex doctor para comprobar acceso a WSFE.")


def main():
    parser = argparse.ArgumentParser(description="Facturación local ARCA para Codex")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup", help="Asistente local de credenciales y perfil")
    sub.add_parser("doctor", help="Validar certificado y consultar puntos de venta sin emitir")
    sub.add_parser("enable-production", help="Habilitación local explícita tras homologación")
    sub.add_parser("serve", help="Iniciar servidor MCP stdio")
    sub.add_parser("paths", help="Mostrar carpeta local privada, nunca secretos")
    args = parser.parse_args()
    try:
        if args.command == "serve":
            from .server import main as serve

            serve()
            return
        root = data_home()
        if args.command == "paths":
            print(root)
        elif args.command == "setup":
            setup(root)
        elif args.command == "doctor":
            from .soap import Wsfe

            p = load_profile(root)
            _, cert = load_pair(p)
            client = Wsfe(p, root)
            print(
                json.dumps(
                    {
                        "environment": p.environment,
                        "certificate_expires": cert.not_valid_after_utc.isoformat(),
                        "health": client.health(),
                        "points_of_sale": client.points(),
                        "iva_conditions": client.iva_conditions(),
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
