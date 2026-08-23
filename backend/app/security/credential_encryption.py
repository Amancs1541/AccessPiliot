from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CredentialEncryptionError(Exception):
    """Raised when a provider credential cannot be encrypted or decrypted."""


def _fernet() -> Fernet:
    key = get_settings().provider_credential_key
    if not key:
        raise CredentialEncryptionError("PROVIDER_CREDENTIAL_KEY is not configured on this server.")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CredentialEncryptionError("PROVIDER_CREDENTIAL_KEY is not a valid encryption key.") from exc


def encrypt_credential(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_credential(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise CredentialEncryptionError("The stored provider credential could not be decrypted.") from exc
