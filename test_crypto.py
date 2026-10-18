"""Unit tests for AES-256-GCM encryption and backward-compatible decryption."""

import json
import os
import tempfile
import pytest

from cleo_crypto import (
    encrypt_data,
    decrypt_data,
    save_encrypted_json,
    load_encrypted_json,
    migrate_unencrypted_file,
    get_encryption_key,
)


def test_encrypt_decrypt_dict_roundtrip():
    payload = {"user": "alice", "roles": ["admin"], "secret": "top-secret-token"}
    envelope = encrypt_data(payload)

    assert envelope["cleo_encrypted"] is True
    assert envelope["cipher"] == "AES-256-GCM"
    assert envelope["version"] == 1
    assert "nonce" in envelope
    assert "ciphertext" in envelope

    decrypted_bytes = decrypt_data(envelope)
    restored = json.loads(decrypted_bytes.decode("utf-8"))
    assert restored == payload


def test_nonce_uniqueness():
    payload = {"message": "hello world"}
    env1 = encrypt_data(payload)
    env2 = encrypt_data(payload)

    assert env1["nonce"] != env2["nonce"]
    assert env1["ciphertext"] != env2["ciphertext"]


def test_save_and_load_encrypted_json():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "sessions.json")
        data = {
            "session-abc": {
                "title": "AWS Spend",
                "messages": [
                    {"role": "user", "content": "What is our spend?"},
                    {"role": "assistant", "content": "Your AWS spend is $12,450."}
                ]
            }
        }

        save_encrypted_json(path, data)

        # Check raw disk content is encrypted envelope
        with open(path, "r", encoding="utf-8") as f:
            raw_disk = json.load(f)

        assert raw_disk.get("cleo_encrypted") is True
        assert raw_disk.get("cipher") == "AES-256-GCM"
        assert "ciphertext" in raw_disk
        assert "AWS spend is $12,450" not in open(path, "r").read()

        # Check transparent loading
        loaded = load_encrypted_json(path)
        assert loaded == data


def test_legacy_plaintext_compatibility():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "legacy_config.json")
        plain = {"GEMINI_API_KEY": "AIzaSyTest123", "theme": "dark"}

        # Write unencrypted legacy JSON directly
        with open(path, "w", encoding="utf-8") as f:
            json.dump(plain, f)

        # load_encrypted_json transparently handles unencrypted JSON
        loaded = load_encrypted_json(path)
        assert loaded == plain


def test_in_place_migration():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "oauth_tokens.json")
        token_data = {"access_token": "secret_token_abc", "refresh_token": "secret_ref_xyz"}

        with open(path, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        # First migration should return True (migrated)
        migrated = migrate_unencrypted_file(path)
        assert migrated is True

        # File is now encrypted on disk
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        assert raw.get("cleo_encrypted") is True

        # Second migration should return False (already encrypted)
        assert migrate_unencrypted_file(path) is False

        # Transparent reading returns exact original data
        assert load_encrypted_json(path) == token_data


def test_tamper_detection():
    payload = {"account": "sensitive"}
    envelope = encrypt_data(payload)

    # Tamper with the ciphertext
    import base64
    raw_ct = bytearray(base64.b64decode(envelope["ciphertext"]))
    raw_ct[0] ^= 0xFF  # Flip bit in ciphertext or auth tag
    envelope["ciphertext"] = base64.b64encode(raw_ct).decode("ascii")

    # AEAD must reject tampered data
    with pytest.raises(Exception):
        decrypt_data(envelope)
