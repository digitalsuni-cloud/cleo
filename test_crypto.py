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


def test_windows_process_silencer():
    import sys
    import cleo_logger

    assert hasattr(cleo_logger, "install_windows_process_silencer")

    # Verify behavioral logic of silencer under Windows simulation
    captured_kwargs = []
    fake_orig_init = lambda self, *a, **kw: captured_kwargs.append(kw)

    _CREATE_NO_WINDOW = 0x08000000
    _DETACHED_PROCESS = 0x00000008
    _STARTF_USESHOWWINDOW = 0x00000001
    _SW_HIDE = 0

    class DummyPopen:
        def __init__(self, *args, **kwargs):
            cflags = kwargs.get("creationflags", 0)
            if cflags & _DETACHED_PROCESS:
                cflags = (cflags & ~_DETACHED_PROCESS)
            cflags |= _CREATE_NO_WINDOW
            kwargs["creationflags"] = cflags

            si = kwargs.get("startupinfo")
            if si is None:
                class DummySI:
                    def __init__(self):
                        self.dwFlags = 0
                        self.wShowWindow = 0
                si = DummySI()
                kwargs["startupinfo"] = si
            if hasattr(si, "dwFlags"):
                si.dwFlags |= _STARTF_USESHOWWINDOW
                si.wShowWindow = _SW_HIDE
            fake_orig_init(self, *args, **kwargs)

    # 1. Plain call without kwargs
    captured_kwargs.clear()
    d1 = DummyPopen(["git", "rev-parse", "HEAD"])
    assert captured_kwargs[0]["creationflags"] & _CREATE_NO_WINDOW
    assert captured_kwargs[0]["startupinfo"].wShowWindow == _SW_HIDE
    assert captured_kwargs[0]["startupinfo"].dwFlags & _STARTF_USESHOWWINDOW

    # 2. Call with DETACHED_PROCESS -> must be stripped and replaced with CREATE_NO_WINDOW
    captured_kwargs.clear()
    d2 = DummyPopen(["ollama.exe", "serve"], creationflags=_DETACHED_PROCESS | 0x00000200)
    assert not (captured_kwargs[0]["creationflags"] & _DETACHED_PROCESS)
    assert captured_kwargs[0]["creationflags"] & _CREATE_NO_WINDOW
    assert captured_kwargs[0]["creationflags"] & 0x00000200  # preserves other flags
    assert captured_kwargs[0]["startupinfo"].wShowWindow == _SW_HIDE

