from __future__ import annotations

import os


class SecretReferenceStore:
    """Local development adapter; production secrets belong in a platform secret store."""

    def resolve(self, reference: str) -> str | None:
        return os.getenv(reference) if reference else None

    def contains_secret_value(self, value: str | None) -> bool:
        return bool(value and any(value == candidate for candidate in os.environ.values()))
