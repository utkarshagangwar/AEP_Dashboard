"""Credential encryption/decryption using Fernet symmetric encryption.

The encryption key must be set via the AI_CREDENTIAL_KEY environment variable.
Generate a key with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""
import json
import os

from app.core.logging import get_logger

logger = get_logger(__name__)

_ephemeral_key: bytes | None = None


def _get_fernet():
    global _ephemeral_key
    from cryptography.fernet import Fernet

    raw = os.environ.get("AI_CREDENTIAL_KEY")
    if raw:
        key = raw.encode() if isinstance(raw, str) else raw
    else:
        if _ephemeral_key is None:
            _ephemeral_key = Fernet.generate_key()
            logger.warning(
                "AI_CREDENTIAL_KEY not set — using ephemeral key. "
                "Credentials will not be decryptable after restart. "
                "Set AI_CREDENTIAL_KEY to a stable Fernet key."
            )
        key = _ephemeral_key

    return Fernet(key)


def encrypt_credentials(creds: dict) -> str:
    """Encrypt a credentials dict and return a Fernet token string."""
    f = _get_fernet()
    return f.encrypt(json.dumps(creds).encode()).decode()


def decrypt_credentials(encrypted: str) -> dict:
    """Decrypt a Fernet token string back to a credentials dict."""
    f = _get_fernet()
    return json.loads(f.decrypt(encrypted.encode()).decode())
