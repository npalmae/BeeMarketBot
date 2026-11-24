"""La API de acceso, bajo ``/api/auth``.

Responde con la forma habitual de la aplicación (``success`` y ``data`` o
``error``), con los mensajes en el idioma de la petición.
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from flask import Blueprint, current_app, jsonify, request, session

from ..utils.locale import t
from ..utils.logger import get_logger
from .passwords import username_key
from .roles import ADMIN
from .service import AuthError, AuthService, Conflict, InvalidCredentials, NotFound
from .sessions import csrf_is_valid, current_user, ensure_csrf, start_session
from .store import User
from .throttle import LoginThrottle

logger = get_logger("mirofish.auth")

auth_bp = Blueprint("auth", __name__)


def _service() -> AuthService:
    return current_app.extensions["auth"]


def _error(status: int, key: str, code: str, **params: Any):
    return jsonify({"success": False, "error": t(key, **params), "code": code}), status


def _failed(error: AuthError):
    status = 409 if isinstance(error, Conflict) else 404 if isinstance(error, NotFound) else 400
    return jsonify({"success": False, "error": t(error.key, **error.params)}), status


def admin_required(view: Callable[..., Any]) -> Callable[..., Any]:
    """Solo para administradores: se comprueba aquí y también en la protección general."""

    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        user = current_user()
        if user is None:
            return _error(401, "auth.error.loginRequired", "login_required")
        if user.role != ADMIN:
            return _error(403, "auth.error.adminOnly", "forbidden")
        return view(user, *args, **kwargs)

    return wrapped


# ---------------------------------------------------------------- sesión


@auth_bp.route("/session", methods=["GET"])
def session_state():
    """Quién está conectado y con qué token debe firmar sus cambios.

    Es público: sin sesión devuelve ``user: null`` y un token nuevo, que es el
    que luego exige el inicio de sesión.
    """

    user = current_user()
    data = {
        "user": user.to_dict() if user else None,
        "csrf": ensure_csrf(),
        # Sin ninguna cuenta, la pantalla de entrada explica cómo crear la primera.
        "setup_needed": _service().is_empty(),
    }
    return jsonify({"success": True, "data": data})


@auth_bp.route("/login", methods=["POST"])
def login():
    if not csrf_is_valid():
        return _error(403, "auth.error.csrfRejected", "csrf_rejected")
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "")
    password = str(data.get("password") or "")
    address = request.remote_addr or ""
    throttle: LoginThrottle = current_app.extensions["auth_throttle"]
    key = username_key(username)

    wait = throttle.retry_after(address, key)
    if wait:
        response = _error(429, "auth.error.tooManyAttempts", "too_many_attempts")
        response[0].headers["Retry-After"] = str(wait)
        return response

    try:
        user = _service().authenticate(username, password)
    except InvalidCredentials:
        throttle.record_failure(address, key)
        logger.warning("Inicio de sesión fallido para '%s' desde %s", username, address)
        return _error(401, "auth.error.invalidCredentials", "invalid_credentials")

    throttle.reset(address, key)
    token = start_session(user.id)
    logger.info("Inicio de sesión correcto: %s", user.username)
    return jsonify({"success": True, "data": {"user": user.to_dict(), "csrf": token}})


@auth_bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"success": True, "data": None})


@auth_bp.route("/password", methods=["POST"])
def change_password():
    """Cambia la contraseña propia, previa comprobación de la actual."""

    user = current_user()
    if user is None:  # pragma: no cover - la protección general ya lo impide
        return _error(401, "auth.error.loginRequired", "login_required")
    data = request.get_json(silent=True) or {}
    try:
        updated = _service().change_password(
            user.id, data.get("current_password"), data.get("new_password")
        )
    except AuthError as error:
        return _failed(error)
    return jsonify({"success": True, "data": {"user": updated.to_dict()}})


# ---------------------------------------------------------------- usuarios (solo administradores)


@auth_bp.route("/users", methods=["GET"])
@admin_required
def list_users(_admin: User):
    users = _service().store.list()
    return jsonify({"success": True, "data": {"users": [item.to_dict() for item in users]}})


@auth_bp.route("/users", methods=["POST"])
@admin_required
def create_user(_admin: User):
    """Crea una cuenta. Tendrá que cambiar la contraseña la primera vez que entre."""

    data = request.get_json(silent=True) or {}
    try:
        user = _service().create_user(data.get("username"), data.get("password"), data.get("role"))
    except AuthError as error:
        return _failed(error)
    logger.info("Cuenta creada: %s (%s)", user.username, user.role)
    return jsonify({"success": True, "data": {"user": user.to_dict()}}), 201


@auth_bp.route("/users/<int:user_id>", methods=["PATCH"])
@admin_required
def update_user(_admin: User, user_id: int):
    """Cambia el nivel de una cuenta o la habilita y deshabilita."""

    data = request.get_json(silent=True) or {}
    try:
        user = _service().store.get(user_id)
        if user is None:
            raise NotFound(user_id)
        if "role" in data:
            user = _service().set_role(user_id, data.get("role"))
        if "disabled" in data:
            user = _service().set_disabled(user_id, data.get("disabled"))
    except AuthError as error:
        return _failed(error)
    return jsonify({"success": True, "data": {"user": user.to_dict()}})


@auth_bp.route("/users/<int:user_id>/password", methods=["POST"])
@admin_required
def reset_password(_admin: User, user_id: int):
    """Fija una contraseña nueva a una cuenta, que deberá cambiarla al entrar."""

    data = request.get_json(silent=True) or {}
    try:
        user = _service().set_password(user_id, data.get("new_password"), must_change=True)
    except AuthError as error:
        return _failed(error)
    return jsonify({"success": True, "data": {"user": user.to_dict()}})
