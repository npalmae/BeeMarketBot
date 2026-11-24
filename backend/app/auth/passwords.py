"""Reglas de contraseña y hash, sin dependencias nuevas."""

from __future__ import annotations

import re
import secrets

from werkzeug.security import check_password_hash, generate_password_hash

MIN_LENGTH = 8
# El hash es lento a propósito; acotar el tamaño evita que lo pidan sobre textos enormes.
MAX_LENGTH = 128

# Sin caracteres que se confundan al copiar a mano (0/O, 1/l/I). Son 56.
GENERATED_ALPHABET = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
GENERATED_LENGTH = 16

_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")

# Hash de una contraseña que no corresponde a ninguna cuenta: sirve para gastar
# el mismo tiempo cuando el usuario no existe y no delatar así qué cuentas hay.
_DUMMY_HASH = generate_password_hash("contrasena-de-referencia-que-nadie-usa")


def username_key(username: str) -> str:
    """Nombre de usuario tal como se compara y guarda: sin espacios y en minúsculas."""

    return str(username or "").strip().lower()


def username_problem(username: object) -> str | None:
    """Clave del mensaje de error si el nombre de usuario no sirve; None si sirve."""

    if not isinstance(username, str) or not _USERNAME.match(username_key(username)):
        return "auth.error.usernameRules"
    return None


def password_problem(username: str, password: object) -> str | None:
    """Clave del mensaje de error si la contraseña no sirve; None si sirve."""

    if not isinstance(password, str) or len(password) < MIN_LENGTH:
        return "auth.error.passwordTooShort"
    if len(password) > MAX_LENGTH:
        return "auth.error.passwordTooLong"
    if password.strip().lower() == username_key(username):
        return "auth.error.passwordEqualsUsername"
    return None


def hash_password(password: str) -> str:
    return generate_password_hash(password[:MAX_LENGTH])


def generate_password(length: int = GENERATED_LENGTH) -> str:
    """Contraseña al azar para asignar a una cuenta; la deberá cambiar al entrar."""

    return "".join(secrets.choice(GENERATED_ALPHABET) for _ in range(length))


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return check_password_hash(password_hash, password[:MAX_LENGTH])
    except (TypeError, ValueError):
        # Un hash mal formado nunca autentica.
        return False


def spend_hashing_time(password: str) -> None:
    """Gasta el tiempo de una comprobación real, sin comparar nada."""

    try:
        check_password_hash(_DUMMY_HASH, str(password)[:MAX_LENGTH])
    except (TypeError, ValueError):  # pragma: no cover - solo si cambia el formato
        pass
