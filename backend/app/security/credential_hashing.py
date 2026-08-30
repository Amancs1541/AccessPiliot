from __future__ import annotations

import hashlib
import hmac
import secrets

"""PBKDF2-HMAC-SHA256 password hashing for the portal's own local credentials (bootstrap + break-glass) — stdlib
only, no new dependency (bcrypt/argon2/passlib), matching this codebase's established zero-new-dependency
discipline. 260,000 iterations matches OWASP's current PBKDF2-SHA256 minimum recommendation."""

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return f"{_ALGORITHM}${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations_str, salt, hash_hex = stored.split("$")
        if algorithm != _ALGORITHM:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), int(iterations_str))
        return hmac.compare_digest(digest.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False
