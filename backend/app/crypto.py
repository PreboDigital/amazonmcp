"""
Field-level encryption for sensitive credential data.

Uses Fernet symmetric encryption from the `cryptography` package.
The key is sourced from the ENCRYPTION_KEY env var.

If no key is configured (development mode), encryption/decryption are
passthrough operations so local development works without extra setup.

Plaintext fallback for legacy unencrypted DB rows is intentional but
**bounded**: production with ``ENCRYPTION_KEY`` set + ``REQUIRE_ENCRYPTED_SECRETS``
truthy will raise instead of silently returning ciphertext as plaintext to
Amazon — that path is the one that bricked our daily syncs in the wild.

Migrate legacy rows by running ``python -m scripts.reencrypt_credentials``.
"""

import logging
import os
from cryptography.fernet import Fernet, InvalidToken
from app.config import get_settings

logger = logging.getLogger(__name__)

_FERNET_TOKEN_PREFIX = "gAAAAA"  # Fernet tokens are URL-safe base64 starting with this constant header
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


def _strict_mode_enabled() -> bool:
    """When true, decrypt failures raise instead of returning ciphertext as-is.

    Defaults to off so existing deployments don't break mid-flight; flip
    ``REQUIRE_ENCRYPTED_SECRETS=1`` once ``scripts/reencrypt_credentials.py``
    has been run successfully against production.
    """
    raw = os.getenv("REQUIRE_ENCRYPTED_SECRETS", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def looks_encrypted(value: str | None) -> bool:
    """Heuristic — true if the value looks like a Fernet token."""
    if not isinstance(value, str) or len(value) < 40:
        return False
    return value.startswith(_FERNET_TOKEN_PREFIX)


def encrypt_value(plaintext: str | None) -> str | None:
    """Encrypt a string value. Returns the ciphertext or the original value if no key."""
    if plaintext is None:
        return None
    f = _get_fernet()
    if f is None:
        return plaintext  # no-op in dev without key
    if looks_encrypted(plaintext):
        # Idempotent — caller passed already-encrypted value (e.g. migration retry)
        return plaintext
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
        if _strict_mode_enabled():
            raise RuntimeError(
                "decrypt_value: stored secret could not be decrypted with the configured "
                "ENCRYPTION_KEY. Run `python -m scripts.reencrypt_credentials` to migrate "
                "legacy plaintext rows, or unset REQUIRE_ENCRYPTED_SECRETS."
            )
        # Legacy plaintext or rotated key. Log loudly but return as-is so the
        # caller can still operate on dev / pre-encryption data.
        logger.warning(
            "Failed to decrypt value — returning as-is. Likely pre-encryption plaintext "
            "in the DB. Run `python -m scripts.reencrypt_credentials` to migrate."
        )
        return ciphertext
