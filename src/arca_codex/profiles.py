"""Each connection has a fixed home/profile; never switch a live issuer implicitly."""

import os
from pathlib import Path

from .storage import load_profile


def list_profiles():
    base = (
        Path(os.environ.get("ARCA_CODEX_HOME", Path.home() / ".arca-codex")).expanduser().resolve()
    )
    out = []
    for path in [base, *sorted((base / "profiles").glob("*"))]:
        if not path.is_dir() or not (path / "profile.json").exists():
            continue
        profile = load_profile(path)
        out.append(
            {
                "name": "default" if path == base else path.name,
                "environment": profile.environment,
                "issuer_cuit": profile.issuer.cuit,
                "tax_regime": profile.issuer.tax_regime,
                "point_of_sale": profile.point_of_sale,
                "production_enabled": profile.production_enabled,
                "certificate_cuit": profile.certificate_cuit or profile.issuer.cuit,
                "delegation_confirmed": profile.delegation_confirmed,
                "ordinary_a_authorized": profile.ordinary_a_authorized,
            }
        )
    return out
