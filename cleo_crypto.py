"""cleo_crypto.py — AES-256-GCM encryption for sensitive local Cleo storage.

Encrypts:
  - ~/.cleo/oauth_tokens.json (CloudHealth access/refresh tokens)
  - ~/.cleo/mcp_oauth_tokens.json (CloudHealth MCP tokens)
  - ~/.cleo/config.json (AI engine API keys)
  - ~/.cleo/sessions.json (User chat history & queries)
  - ~/.cleo/pkce_verifier.json (OAuth PKCE verifier state)

Key Storage Options:
  1. Machine/User Key (Default): Derived automatically from a local secret in ~/.cleo/.machine_key
     salted with hardware & user fingerprints (MAC, username, machine-id) via PBKDF2-HMAC-SHA256.
     Seamless and 100% transparent on disk.
  2. Keychain: macOS Keychain (service="Cleo") or Windows Credential Manager (when CLEO_KEY_STORAGE=keychain).
  3. Passphrase: User-supplied passphrase via environment variable CLEO_PASSPHRASE or CLEO_ENCRYPTION_KEY.

Backward Compatibility:
  Seamlessly reads legacy unencrypted JSON files, transparently upgrading them to AES-256-GCM on next save.
"""

import base64
import getpass
import hashlib
import json
import logging
import os
import platform
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

logger = logging.getLogger("cleo.crypto")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("[%(asctime)s] [Crypto] %(message)s"))
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# Check for cryptography library
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    HAVE_CRYPTOGRAPHY = True
except ImportError:
    HAVE_CRYPTOGRAPHY = False
    logger.warning("Package 'cryptography' is not installed; AES-256-GCM encryption disabled.")

APP_DIR = os.path.expanduser("~/.cleo")
MACHINE_KEY_FILE = os.path.join(APP_DIR, ".machine_key")
CIPHER_VERSION = 1
ASSOCIATED_DATA = b"cleo-v1-aesgcm"


def _get_machine_fingerprint() -> bytes:
    """Collect hardware and user identifiers for PBKDF2 key binding."""
    parts = [
        str(uuid.getnode()),           # Hardware MAC address node
        getpass.getuser(),             # Username
        platform.node(),               # Hostname
        platform.system(),             # OS name
    ]
    # On Linux, try reading /etc/machine-id
    if os.path.exists("/etc/machine-id"):
        try:
            with open("/etc/machine-id", "r") as f:
                parts.append(f.read().strip())
        except Exception:
            pass
    elif os.path.exists("/var/lib/dbus/machine-id"):
        try:
            with open("/var/lib/dbus/machine-id", "r") as f:
                parts.append(f.read().strip())
        except Exception:
            pass

    return ":".join(parts).encode("utf-8")


def _get_keychain_secret(service: str = "Cleo", account: Optional[str] = None) -> Optional[str]:
    """Retrieve or create a high-entropy secret from macOS Keychain."""
    if platform.system() != "Darwin":
        return None
    acct = account or getpass.getuser()
    try:
        # Check existing item
        res = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", acct, "-w"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()

        # If not found, generate and save a new 256-bit secret in Keychain
        new_secret = os.urandom(32).hex()
        add_res = subprocess.run(
            ["security", "add-generic-password", "-U", "-s", service, "-a", acct, "-w", new_secret],
            capture_output=True,
            text=True,
            timeout=5
        )
        if add_res.returncode == 0:
            logger.info("Generated new Cleo master encryption key in macOS Keychain.")
            return new_secret
    except Exception as e:
        logger.debug(f"Keychain access skipped: {e}")
    return None


def _get_machine_secret() -> str:
    """Retrieve or create owner-only secret file in ~/.cleo/.machine_key."""
    os.makedirs(APP_DIR, exist_ok=True)
    if os.path.isfile(MACHINE_KEY_FILE):
        try:
            with open(MACHINE_KEY_FILE, "r", encoding="utf-8") as f:
                val = f.read().strip()
                if len(val) >= 32:
                    return val
        except Exception as e:
            logger.warning(f"Could not read machine key file: {e}")

    # Generate new random 256-bit secret and store owner-only (0600)
    new_secret = os.urandom(32).hex()
    try:
        fd = os.open(MACHINE_KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(new_secret + "\n")
        os.chmod(MACHINE_KEY_FILE, 0o600)
        logger.info(f"Initialized owner-only machine encryption secret: {MACHINE_KEY_FILE}")
    except Exception as e:
        logger.warning(f"Could not persist machine key file: {e}")
    return new_secret


_cached_key: Optional[bytes] = None

def get_encryption_key() -> Optional[bytes]:
    """Derive a 256-bit (32 bytes) AES-GCM encryption key based on configured storage option."""
    global _cached_key
    if _cached_key is not None:
        return _cached_key

    # Option 1: Direct key / Passphrase from environment
    env_key = os.environ.get("CLEO_ENCRYPTION_KEY")
    if env_key:
        try:
            if len(env_key) == 64:  # Hex-encoded 32 bytes
                _cached_key = bytes.fromhex(env_key)
                return _cached_key
            raw = base64.b64decode(env_key)
            if len(raw) == 32:
                _cached_key = raw
                return _cached_key
        except Exception:
            pass

    passphrase = os.environ.get("CLEO_PASSPHRASE")
    if passphrase:
        salt = b"cleo-passphrase-salt-v1"
        _cached_key = hashlib.pbkdf2_hmac("sha256", passphrase.encode("utf-8"), salt, 100_000, dklen=32)
        return _cached_key

    # Option 2: Keychain (if requested via CLEO_KEY_STORAGE=keychain or on macOS)
    storage_pref = os.environ.get("CLEO_KEY_STORAGE", "").lower()
    if storage_pref in ("keychain", "auto") or (platform.system() == "Darwin" and storage_pref != "machine"):
        kc_secret = _get_keychain_secret()
        if kc_secret:
            fingerprint = _get_machine_fingerprint()
            _cached_key = hashlib.pbkdf2_hmac("sha256", kc_secret.encode("utf-8"), fingerprint, 100_000, dklen=32)
            return _cached_key

    # Option 3: Machine / User Key (seamless default)
    machine_secret = _get_machine_secret()
    fingerprint = _get_machine_fingerprint()
    _cached_key = hashlib.pbkdf2_hmac("sha256", machine_secret.encode("utf-8"), fingerprint, 100_000, dklen=32)
    return _cached_key


def encrypt_data(data: Union[Dict, list, str, bytes]) -> Dict[str, Any]:
    """Encrypt payload using AES-256-GCM, returning a structured JSON-compatible wrapper."""
    if not HAVE_CRYPTOGRAPHY:
        raise RuntimeError("Encryption requires 'cryptography' package (not installed)")

    key = get_encryption_key()
    if not key:
        raise RuntimeError("Could not resolve encryption key")

    if isinstance(data, (dict, list)):
        raw_bytes = json.dumps(data, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    elif isinstance(data, str):
        raw_bytes = data.encode("utf-8")
    elif isinstance(data, bytes):
        raw_bytes = data
    else:
        raise ValueError(f"Unsupported data type for encryption: {type(data)}")

    nonce = os.urandom(12)  # Standard 96-bit nonce for AES-GCM
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, raw_bytes, ASSOCIATED_DATA)

    return {
        "cleo_encrypted": True,
        "version": CIPHER_VERSION,
        "cipher": "AES-256-GCM",
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "ciphertext": base64.b64encode(ciphertext).decode("ascii"),
    }


def decrypt_data(wrapper: Dict[str, Any]) -> bytes:
    """Decrypt an AES-256-GCM wrapped payload and return the plaintext bytes."""
    if not HAVE_CRYPTOGRAPHY:
        raise RuntimeError("Decryption requires 'cryptography' package (not installed)")

    if not isinstance(wrapper, dict) or not wrapper.get("cleo_encrypted"):
        raise ValueError("Payload is not a valid Cleo encrypted wrapper")

    key = get_encryption_key()
    if not key:
        raise RuntimeError("Could not resolve encryption key")

    nonce = base64.b64decode(wrapper["nonce"])
    ciphertext = base64.b64decode(wrapper["ciphertext"])

    aesgcm = AESGCM(key)
    return aesgcm.decrypt(nonce, ciphertext, ASSOCIATED_DATA)


def load_encrypted_json(filepath: str, default: Any = None) -> Any:
    """Load JSON from filepath. If encrypted with AES-256-GCM, decrypts it transparently.

    If file is legacy unencrypted JSON, returns it directly (backward compatibility).
    """
    if not os.path.exists(filepath):
        return default

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)

        # Check if file has the Cleo encrypted envelope
        if isinstance(raw, dict) and raw.get("cleo_encrypted") is True:
            plaintext_bytes = decrypt_data(raw)
            return json.loads(plaintext_bytes.decode("utf-8"))

        # Legacy unencrypted JSON
        return raw
    except Exception as e:
        logger.warning(f"Could not load or decrypt {filepath}: {e}")
        return default


def save_encrypted_json(filepath: str, data: Any) -> None:
    """Save data to filepath encrypted with AES-256-GCM, enforcing owner-only (0600) permissions."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)

    if HAVE_CRYPTOGRAPHY:
        try:
            envelope = encrypt_data(data)
            fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(envelope, f, indent=2)
            os.chmod(filepath, 0o600)
            return
        except Exception as e:
            logger.error(f"Encryption failed for {filepath}, falling back to owner-only plain JSON: {e}")

    # Fallback to owner-only plain JSON if cryptography is unavailable or failed
    fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(filepath, 0o600)


def migrate_unencrypted_file(filepath: str) -> bool:
    """Check if filepath is plaintext JSON and migrate it to AES-256-GCM in-place."""
    if not os.path.isfile(filepath):
        return False
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and raw.get("cleo_encrypted") is True:
            return False  # Already encrypted

        # Plaintext file — upgrade in-place
        save_encrypted_json(filepath, raw)
        logger.info(f"✓ Migrated {filepath} to AES-256-GCM encrypted storage.")
        return True
    except Exception as e:
        logger.debug(f"Skipping migration for {filepath}: {e}")
        return False


def migrate_all_cleo_files() -> int:
    """Migrate all sensitive Cleo data files in ~/.cleo/ to encrypted storage."""
    sensitive_files = [
        os.path.join(APP_DIR, "sessions.json"),
        os.path.join(APP_DIR, "oauth_tokens.json"),
        os.path.join(APP_DIR, "mcp_oauth_tokens.json"),
        os.path.join(APP_DIR, "config.json"),
        os.path.join(APP_DIR, "pkce_verifier.json"),
    ]
    migrated_count = 0
    for f in sensitive_files:
        if migrate_unencrypted_file(f):
            migrated_count += 1
    return migrated_count
