"""Protección de toda la API y creación del primer administrador.

``install(app)`` se llama una vez, al armar la aplicación:

- abre el almacén de cuentas;
- crea el primer administrador si no hay cuentas y el entorno trae
  ``ADMIN_USERNAME`` y ``ADMIN_PASSWORD``;
- registra la comprobación que hace falta en cada petición.
"""

from __future__ import annotations

from typing import Any

from flask import Flask, jsonify, request

from ..utils.locale import t
from ..utils.logger import get_logger
from .roles import ADMIN, WRITE_ROLES
from .service import AuthError, AuthService
from .sessions import csrf_is_valid, current_user, remember_user
from .store import UserStore
from .throttle import LoginThrottle

logger = get_logger("mirofish.auth")

# Niveles que puede exigir una ruta, de menor a mayor.
READ = "read"
WRITE = "write"

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Lo único a lo que se llega sin sesión.
PUBLIC = frozenset({("GET", "/api/auth/session"), ("POST", "/api/auth/login")})

# Rutas POST que solo consultan: un visor puede usarlas.
READ_ONLY_POST = frozenset(
    {
        "/api/simulation/prepare/status",
        "/api/simulation/env-status",
        "/api/simulation/interview/history",
        "/api/report/generate/status",
        "/api/report/tools/search",
        "/api/report/tools/statistics",
    }
)


def access_required(method: str, path: str) -> str:
    """Nivel mínimo para una petición."""

    if path == "/api/auth/users" or path.startswith("/api/auth/users/"):
        return ADMIN
    if path == "/api/auth/" or path.startswith("/api/auth/"):
        # Cerrar sesión y cambiar la contraseña propia corresponden a cualquier cuenta.
        return READ
    if method in SAFE_METHODS:
        return READ
    if method == "POST" and path in READ_ONLY_POST:
        return READ
    return WRITE


def _rejected(status: int, key: str, code: str, **params: Any):
    body = {"success": False, "error": t(key, **params), "code": code}
    return jsonify(body), status


def protect():
    """Comprueba sesión, nivel y token en cada petición a la API."""

    if request.method in ("OPTIONS", "HEAD"):
        return None
    path = request.path
    if not path.startswith("/api/") or (request.method, path) in PUBLIC:
        return None

    user = current_user()
    if user is None:
        return _rejected(401, "auth.error.loginRequired", "login_required")
    remember_user(user)

    required = access_required(request.method, path)
    if required == ADMIN and user.role != ADMIN:
        return _rejected(403, "auth.error.adminOnly", "forbidden")
    if required == WRITE and user.role not in WRITE_ROLES:
        return _rejected(403, "auth.error.readOnly", "forbidden")
    if request.method not in SAFE_METHODS and not csrf_is_valid():
        return _rejected(403, "auth.error.csrfRejected", "csrf_rejected")
    return None


def bootstrap_admin(app: Flask, service: AuthService) -> None:
    """Crea el primer administrador cuando corresponde, y avisa de cómo hacerlo si no."""

    if not service.is_empty():
        return
    username = app.config.get("ADMIN_USERNAME")
    password = app.config.get("ADMIN_PASSWORD")
    if username and password:
        try:
            service.bootstrap_admin(username, password)
        except AuthError as error:
            logger.error(
                "No se pudo crear el administrador inicial '%s': %s",
                username,
                t(error.key, **error.params),
            )
        else:
            logger.info(
                "Administrador inicial creado: %s. Tendrá que cambiar la contraseña al entrar.",
                username,
            )
        return
    logger.warning(
        "No hay ninguna cuenta de usuario. Cree el primer administrador con: "
        "python -m app.auth create-user --username <nombre> --role admin"
    )


def warn_about_session_key(app: Flask) -> None:
    if not app.config.get("SECRET_KEY_FROM_ENV"):
        logger.warning(
            "SECRET_KEY no está definida en el entorno (o todavía tiene el valor de ejemplo): "
            "se generó una al arrancar y las sesiones abiertas se cerrarán en cada reinicio."
        )


def install(app: Flask) -> AuthService:
    """Prepara el acceso en la aplicación y devuelve el servicio de cuentas."""

    service = AuthService(UserStore(app.config["AUTH_DB_PATH"]))
    app.extensions["auth"] = service
    app.extensions["auth_throttle"] = LoginThrottle()
    app.before_request(protect)
    warn_about_session_key(app)
    bootstrap_admin(app, service)
    return service


__all__ = ["ADMIN", "READ", "WRITE", "access_required", "install", "protect"]
