from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from config import config

@lru_cache(maxsize=4)
def _ciphers(current_key: str, next_key: str) -> tuple[Fernet, Fernet | None, Fernet | MultiFernet]:
    current = Fernet(current_key.encode())
    if not next_key:
        return current, None, current
    next_cipher = Fernet(next_key.encode())
    if next_key == current_key:
        raise ValueError("ENCRYPTION_KEY_NEXT must differ from ENCRYPTION_KEY")
    return current, next_cipher, MultiFernet([next_cipher, current])


def _get_ciphers() -> tuple[Fernet, Fernet | None, Fernet | MultiFernet]:
    return _ciphers(config.ENCRYPTION_KEY, config.ENCRYPTION_KEY_NEXT)


def validate_encryption_keys() -> None:
    """Reject malformed rotation settings before serving requests."""
    _, next_cipher, _ = _get_ciphers()
    if config.ENCRYPTION_WRITE_NEXT and next_cipher is None:
        raise ValueError("ENCRYPTION_WRITE_NEXT requires ENCRYPTION_KEY_NEXT")


def encrypt(value: str) -> str:
    current, next_cipher, _ = _get_ciphers()
    if config.ENCRYPTION_WRITE_NEXT and next_cipher is None:
        raise ValueError("ENCRYPTION_WRITE_NEXT requires ENCRYPTION_KEY_NEXT")
    writer = next_cipher if config.ENCRYPTION_WRITE_NEXT else current
    return writer.encrypt(value.encode()).decode()


def decrypt(value: str) -> str:
    _, _, reader = _get_ciphers()
    return reader.decrypt(value.encode()).decode()


def rotate_encrypted_token(value: str) -> tuple[str, bool]:
    """Return ciphertext under NEXT and whether the stored value changed.

    Never return or log plaintext. An unreadable token raises InvalidToken so
    a database transaction can roll back instead of partially rotating data.
    """
    current, next_cipher, _ = _get_ciphers()
    if next_cipher is None:
        raise RuntimeError("ENCRYPTION_KEY_NEXT is required for rotation")
    token = value.encode()
    try:
        next_cipher.decrypt(token)
        return value, False
    except InvalidToken:
        plaintext = current.decrypt(token)
        return next_cipher.encrypt(plaintext).decode(), True
