from __future__ import annotations

import os
from pathlib import Path

_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class SecretReferenceStore:
    """Local development adapter; production secrets belong in a platform secret store.

    A `configuration_ref` is only ever a reference (an environment variable name), never the
    secret value itself. The value lives in the process environment / .env file, not the database.
    """

    def resolve(self, reference: str) -> str | None:
        return os.getenv(reference) if reference else None

    def contains_secret_value(self, value: str | None) -> bool:
        return bool(value and any(value == candidate for candidate in os.environ.values()))

    def write(self, reference: str, value: str) -> None:
        os.environ[reference] = value
        lines = _ENV_FILE.read_text().splitlines() if _ENV_FILE.exists() else []
        replaced = False
        for index, line in enumerate(lines):
            if line.startswith(f"{reference}="):
                lines[index] = f"{reference}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{reference}={value}")
        _ENV_FILE.write_text("\n".join(lines) + "\n")
