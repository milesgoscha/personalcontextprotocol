"""Admin token encryption using Fernet with HKDF-derived per-user keys.

Security model:
1. Master key stored in secrets manager (env var for now)
2. Per-user keys derived via HKDF: key = HKDF(master_key, user_id)
3. Admin tokens encrypted with Fernet using derived key
4. Key version stored with ciphertext for rotation support

The admin token is only decrypted on-demand when making requests
to the user's node, and never persisted to logs or disk.
"""

import base64
from typing import NamedTuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings


class EncryptedToken(NamedTuple):
    """Encrypted token with version for key rotation."""

    ciphertext: bytes
    key_version: int


class DecryptionError(Exception):
    """Raised when token decryption fails."""

    pass


def _derive_key(master_key: bytes, user_id: str) -> bytes:
    """Derive a per-user Fernet key using HKDF.

    Args:
        master_key: The master encryption key.
        user_id: The user's UUID (used as salt/info).

    Returns:
        A 32-byte key suitable for Fernet (will be base64 encoded).
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=user_id.encode(),
        info=b"pcp-admin-token",
    )
    derived = hkdf.derive(master_key)
    # Fernet requires base64-encoded 32-byte key
    return base64.urlsafe_b64encode(derived)


def encrypt_token(token: str, user_id: str) -> EncryptedToken:
    """Encrypt an admin token for storage.

    Args:
        token: The plaintext admin token from the PCP node.
        user_id: The user's UUID for key derivation.

    Returns:
        EncryptedToken containing ciphertext and key version.
    """
    settings = get_settings()
    version = settings.current_key_version
    master_key = settings.get_encryption_key(version)

    derived_key = _derive_key(master_key, user_id)
    fernet = Fernet(derived_key)

    ciphertext = fernet.encrypt(token.encode())
    return EncryptedToken(ciphertext=ciphertext, key_version=version)


def decrypt_token(ciphertext: bytes, user_id: str, key_version: int) -> str:
    """Decrypt an admin token.

    Args:
        ciphertext: The encrypted token bytes.
        user_id: The user's UUID for key derivation.
        key_version: The key version used for encryption.

    Returns:
        The plaintext admin token.

    Raises:
        DecryptionError: If decryption fails (wrong key, corrupted data, etc.)
    """
    settings = get_settings()

    # Try the specified version first
    versions_to_try = [key_version]

    # Also try current version in case of migration
    if settings.current_key_version != key_version:
        versions_to_try.append(settings.current_key_version)

    last_error = None
    for version in versions_to_try:
        try:
            master_key = settings.get_encryption_key(version)
            derived_key = _derive_key(master_key, user_id)
            fernet = Fernet(derived_key)
            plaintext = fernet.decrypt(ciphertext)
            return plaintext.decode()
        except InvalidToken as e:
            last_error = e
            continue

    raise DecryptionError(f"Failed to decrypt token: {last_error}")


def rotate_token_encryption(
    ciphertext: bytes,
    user_id: str,
    old_version: int,
) -> EncryptedToken | None:
    """Re-encrypt a token with the current key version.

    Used during key rotation to migrate tokens to new keys.

    Args:
        ciphertext: The encrypted token bytes.
        user_id: The user's UUID.
        old_version: The key version used for current encryption.

    Returns:
        New EncryptedToken with current key version, or None if already current.
    """
    settings = get_settings()
    current_version = settings.current_key_version

    if old_version == current_version:
        return None

    # Decrypt with old key
    plaintext = decrypt_token(ciphertext, user_id, old_version)

    # Re-encrypt with current key
    return encrypt_token(plaintext, user_id)
