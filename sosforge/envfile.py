"""Load ./.env.local (gitignored) so tokens live in one untracked file; .env.example lists the names."""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path, environ=None) -> list[str]:
    """Set KEY=VALUE pairs from a dotenv-style file without overriding variables already set, returning the names loaded. Tested by test_cli.py::test_env_local_loaded_without_override."""
    environ = os.environ if environ is None else environ
    path = Path(path)
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and value and key not in environ:
            environ[key] = value
            loaded.append(key)
    return loaded
