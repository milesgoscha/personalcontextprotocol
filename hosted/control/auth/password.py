"""Password hashing using Argon2.

Argon2 is the winner of the Password Hashing Competition and is
recommended for secure password storage.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

# Configure Argon2 with secure defaults
# These parameters balance security and performance
_hasher = PasswordHasher(
    time_cost=3,        # Number of iterations
    memory_cost=65536,  # 64 MB memory usage
    parallelism=4,      # Number of parallel threads
    hash_len=32,        # Length of the hash in bytes
    salt_len=16,        # Length of the salt in bytes
)


def hash_password(password: str) -> str:
    """Hash a password using Argon2id.

    Args:
        password: The plaintext password to hash.

    Returns:
        The hashed password string (includes algorithm, parameters, salt, and hash).
    """
    return _hasher.hash(password)


def verify_password(password: str, hash: str) -> bool:
    """Verify a password against a hash.

    Args:
        password: The plaintext password to verify.
        hash: The stored password hash.

    Returns:
        True if the password matches, False otherwise.
    """
    try:
        _hasher.verify(hash, password)
        return True
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(hash: str) -> bool:
    """Check if a password hash needs to be rehashed.

    This is useful when upgrading hash parameters. If True, the password
    should be rehashed with the new parameters after successful verification.

    Args:
        hash: The stored password hash.

    Returns:
        True if the hash should be regenerated with current parameters.
    """
    return _hasher.check_needs_rehash(hash)
