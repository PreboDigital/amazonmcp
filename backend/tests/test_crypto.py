"""Tests for app.crypto field-level encryption."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import app.crypto as crypto_mod  # noqa: E402


@pytest.fixture
def with_key():
    """Configure crypto module with a deterministic Fernet key for the test."""
    key = Fernet.generate_key().decode()
    with patch.object(crypto_mod, "_fernet", None):
        with patch("app.crypto.get_settings") as mock_settings:
            mock_settings.return_value.encryption_key = key
            mock_settings.return_value.is_production = False
            yield key
    # Reset singleton
    crypto_mod._fernet = None


def test_encrypt_then_decrypt_roundtrip(with_key):
    cipher = crypto_mod.encrypt_value("super-secret")
    assert cipher != "super-secret"
    assert crypto_mod.looks_encrypted(cipher)
    assert crypto_mod.decrypt_value(cipher) == "super-secret"


def test_encrypt_value_is_idempotent_for_existing_token(with_key):
    cipher = crypto_mod.encrypt_value("hello")
    again = crypto_mod.encrypt_value(cipher)
    assert again == cipher  # not double-encrypted


def test_decrypt_legacy_plaintext_returns_as_is_outside_strict_mode(with_key):
    os.environ.pop("REQUIRE_ENCRYPTED_SECRETS", None)
    assert crypto_mod.decrypt_value("legacy-plain") == "legacy-plain"


def test_decrypt_raises_in_strict_mode(with_key):
    os.environ["REQUIRE_ENCRYPTED_SECRETS"] = "1"
    try:
        with pytest.raises(RuntimeError, match="reencrypt_credentials"):
            crypto_mod.decrypt_value("legacy-plain")
    finally:
        os.environ.pop("REQUIRE_ENCRYPTED_SECRETS", None)


def test_looks_encrypted_heuristic():
    assert not crypto_mod.looks_encrypted(None)
    assert not crypto_mod.looks_encrypted("")
    assert not crypto_mod.looks_encrypted("plain-secret")
    assert not crypto_mod.looks_encrypted("gAAAA")  # too short
    # Real Fernet token starts with gAAAAA and is well over 40 chars
    f = Fernet(Fernet.generate_key())
    token = f.encrypt(b"hello").decode()
    assert crypto_mod.looks_encrypted(token)


def test_passthrough_when_no_key_configured():
    with patch.object(crypto_mod, "_fernet", None):
        with patch("app.crypto.get_settings") as mock_settings:
            mock_settings.return_value.encryption_key = None
            mock_settings.return_value.is_production = False
            assert crypto_mod.encrypt_value("plain") == "plain"
            assert crypto_mod.decrypt_value("plain") == "plain"
    crypto_mod._fernet = None
