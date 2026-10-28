from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .models import Profile


def data_home() -> Path:
    path = (
        Path(os.environ.get("ARCA_CODEX_HOME", Path.home() / ".arca-codex")).expanduser().resolve()
    )
    profile_name = os.environ.get("ARCA_CODEX_PROFILE")
    if profile_name:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,47}", profile_name):
            raise ValueError("Nombre de perfil inválido: usar letras, dígitos, _ o -")
        path = (path / "profiles" / profile_name).resolve()
    # Credentials and journals must not live inside a Git working tree.
    if any((p / ".git").exists() for p in [path, *path.parents]):
        raise ValueError("ARCA_CODEX_HOME debe estar fuera de cualquier repositorio Git")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt":
        path.chmod(0o700)
    return path


def write_private(path: Path, data: bytes):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def load_profile(root: Path) -> Profile:
    path = root / "profile.json"
    if not path.exists():
        raise ValueError("Falta configuración. Ejecutá arca-codex setup en tu terminal local.")
    return Profile.model_validate_json(path.read_text())


@contextmanager
def database(root: Path):
    path = root / "journal.sqlite3"
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    if os.name != "nt":
        path.chmod(0o600)
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("""CREATE TABLE IF NOT EXISTS drafts (
        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, request_key TEXT NOT NULL,
        fingerprint TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
        number INTEGER NOT NULL, result TEXT, created_at TEXT NOT NULL,
        UNIQUE(tenant, request_key))""")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def decode_row(row):
    if row is None:
        raise ValueError("Borrador no encontrado")
    out = dict(row)
    out["payload"] = json.loads(out["payload"])
    out["result"] = json.loads(out["result"]) if out["result"] else None
    return out
