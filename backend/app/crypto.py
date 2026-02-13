"""
Field-level encryption for sensitive credential data.

Uses Fernet symmetric encryption from the `cryptography` package.
The key is sourced from the ENCRYPTION_KEY env var.

If no key is configured (development mode), encryption/decryption are
passthrough operations so local development works without extra setup.
"""

import logging
from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings

logger = logging.getLogger(__name__)

_fernet = None
_NO_KEY_WARNING_EMITTED = False


def _get_fernet() -> Fernet | None:
    """Lazy-init the Fernet instance from the configured key."""
    global _fernet, _NO_KEY_WARNING_EMITTED
    if _fernet is not None:
        return _fernet

    settings = get_settings()
    key = settings.encryption_key

    if not key:
        if settings.is_production:
            raise RuntimeError(
                "ENCRYPTION_KEY must be set in production. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
        if not _NO_KEY_WARNING_EMITTED:
            logger.warning(
                "ENCRYPTION_KEY not set — credential secrets will be stored in plaintext. "
                "This is acceptable for local development only."
            )
            _NO_KEY_WARNING_EMITTED = True
        return None

    try:
        _fernet = Fernet(key.encode() if isinstance(key, str) else key)
    except Exception as exc:
        raise RuntimeError(f"Invalid ENCRYPTION_KEY: {exc}") from exc

    return _fernet


def encrypt_value(plaintext: str | None) -> str | None:
    """Encrypt a string value. Returns the ciphertext or the original value if no key."""
    if plaintext is None:
        return None
    f = _get_fernet()
    if f is None:
        return plaintext  # no-op in dev without key
    return f.encrypt(plaintext.encode()).decode()


def decrypt_value(ciphertext: str | None) -> str | None:
    """Decrypt a string value. Returns the plaintext or the original value if no key."""
    if ciphertext is None:
        return None
    f = _get_fernet()
    if f is None:
        return ciphertext  # no-op in dev without key
    try:
        return f.decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        # Value might be stored in plaintext from before encryption was enabled
        logger.warning("Failed to decrypt value — returning as-is (may be pre-encryption plaintext).")
        return ciphertext
