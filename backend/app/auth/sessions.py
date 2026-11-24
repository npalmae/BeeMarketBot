"""Sesiones: quién está conectado y el token anti-CSRF.

La sesión viaja en una cookie firmada por Flask, con ``HttpOnly`` y ``SameSite=Lax``
(se fija en la configuración). El servidor vuelve a leer la cuenta en cada
petición, así que una cuenta deshabilitada o borrada pierde el acceso de inmediato.
"""

from __future__ import annotations

import hmac
import secrets
from typing import Any

from flask import current_app, g, request, session

from .service import AuthService, User

SESSION_USER = "user_id"
SESSION_CSRF = "csrf"
CSRF_HEADER = "X-CSRF-Token"


def start_session(user_id: int) -> str:
    """Abre una sesión nueva para la cuenta y devuelve su token anti-CSRF.

    Se descarta todo lo que hubiera en la sesión anterior, incluso anónima:
    así nadie puede hacer que otra persona use un identificador conocido de antemano.
    """

    session.clear()
    session.permanent = True
    session[SESSION_USER] = user_id
    session[SESSION_CSRF] = secrets.token_hex(32)
    return str(session[SESSION_CSRF])


def ensure_csrf() -> str:
    """Token anti-CSRF de la sesión actual, creándolo si no existe."""

    if not session.get(SESSION_CSRF):
        session[SESSION_CSRF] = secrets.token_hex(32)
    return str(session[SESSION_CSRF])


def csrf_is_valid() -> bool:
    """Comprueba que la petición trae el token de su propia sesión."""

    expected = session.get(SESSION_CSRF)
    sent = request.headers.get(CSRF_HEADER, "")
    return bool(expected) and hmac.compare_digest(str(expected), str(sent))


def current_user() -> User | None:
    """Cuenta de la sesión, si sigue activa. Se lee una vez por petición."""

    if "user" in g:
        return g.user  # type: ignore[attr-defined]
    service: AuthService | None = current_app.extensions.get("auth")
    user_id = session.get(SESSION_USER)
    user = None
    if service is not None and isinstance(user_id, int):
        user = service.store.get(user_id)
        if user is not None and user.disabled:
            user = None
    g.user = user
    return user


def remember_user(user: Any) -> None:
    g.user = user


__all__ = ["CSRF_HEADER", "csrf_is_valid", "current_user", "ensure_csrf", "start_session"]
