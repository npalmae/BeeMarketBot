"""Operaciones sobre las cuentas y sus reglas.

Los errores que se muestran a quien llama llevan la clave de un mensaje
traducido (``AuthError.key``) en vez de texto, para que cada petición lo reciba
en su idioma.
"""

from __future__ import annotations

from .passwords import (
    hash_password,
    password_problem,
    spend_hashing_time,
    username_problem,
    verify_password,
)
from .roles import ADMIN, is_valid_role
from .store import User, UserStore, UsernameTaken


class AuthError(Exception):
    """Un error que se le muestra al usuario."""

    def __init__(self, key: str, **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.params = params


class InvalidCredentials(AuthError):
    """Nombre de usuario o contraseña incorrectos, o cuenta deshabilitada."""

    def __init__(self) -> None:
        super().__init__("auth.error.invalidCredentials")


class InvalidInput(AuthError):
    """Datos inválidos."""


class NotFound(InvalidInput):
    """La cuenta no existe."""

    def __init__(self, user_id: object = None) -> None:
        super().__init__("auth.error.userNotFound")


class Conflict(AuthError):
    """La operación rompería una regla (nombre repetido, último administrador)."""


class AuthService:
    """Reglas de negocio sobre el almacén de cuentas."""

    def __init__(self, store: UserStore) -> None:
        self.store = store

    def is_empty(self) -> bool:
        return self.store.count() == 0

    def bootstrap_admin(self, username: str, password: str) -> bool:
        """Crea el primer administrador si no hay ninguna cuenta. Dice si lo creó."""

        if not self.is_empty():
            return False
        self.create_user(username, password, ADMIN)
        return True

    def authenticate(self, username: object, password: object) -> User:
        """Devuelve la cuenta si las credenciales sirven; no revela cuál falló."""

        user = self.store.find(str(username or ""))
        if user is None:
            # Mismo gasto de tiempo que una comprobación real.
            spend_hashing_time(str(password or ""))
            raise InvalidCredentials()
        if not verify_password(user.password_hash, str(password or "")) or user.disabled:
            raise InvalidCredentials()
        return user

    def create_user(self, username: object, password: object, role: object) -> User:
        problem = username_problem(username) or password_problem(str(username or ""), password)
        if problem:
            raise InvalidInput(problem)
        if not is_valid_role(role):
            raise InvalidInput("auth.error.invalidRole")
        try:
            return self.store.create(
                str(username).strip(), hash_password(str(password)), str(role), must_change_password=True
            )
        except UsernameTaken as error:
            raise Conflict("auth.error.usernameTaken") from error

    def set_role(self, user_id: object, role: object) -> User:
        user = self._existing(user_id)
        if not is_valid_role(role):
            raise InvalidInput("auth.error.invalidRole")
        if user.role != role and user.role == ADMIN and self.store.active_admins() <= 1:
            raise Conflict("auth.error.lastAdmin")
        return self.store.update(user.id, role=str(role))

    def set_disabled(self, user_id: object, disabled: object) -> User:
        if not isinstance(disabled, bool):
            raise InvalidInput("auth.error.mustBeBoolean")
        user = self._existing(user_id)
        if disabled and user.role == ADMIN and self.store.active_admins() <= 1:
            raise Conflict("auth.error.lastAdmin")
        return self.store.update(user.id, disabled=disabled)

    def set_password(self, user_id: object, password: object, *, must_change: bool) -> User:
        """Fija una contraseña. ``must_change`` marca que debe cambiarla al entrar."""

        user = self._existing(user_id)
        problem = password_problem(user.username, password)
        if problem:
            raise InvalidInput(problem)
        return self.store.update(
            user.id,
            password_hash=hash_password(str(password)),
            must_change_password=bool(must_change),
        )

    def change_password(self, user_id: object, current_password: object, password: object) -> User:
        """Cambia la contraseña propia, previa comprobación de la actual."""

        user = self._existing(user_id)
        if not verify_password(user.password_hash, str(current_password or "")):
            raise InvalidInput("auth.error.wrongCurrentPassword")
        return self.set_password(user.id, password, must_change=False)

    def _existing(self, user_id: object) -> User:
        try:
            user = self.store.get(int(user_id))  # type: ignore[arg-type]
        except (TypeError, ValueError) as error:
            raise NotFound(user_id) from error
        if user is None:
            raise NotFound(user_id)
        return user


__all__ = ["ADMIN", "AuthError", "AuthService", "Conflict", "InvalidCredentials", "InvalidInput", "NotFound"]
