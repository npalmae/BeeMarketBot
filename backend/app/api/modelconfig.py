"""Configuración de los servicios de IA desde la interfaz (solo administradores).

Ver app/modelconfig.py: los valores se guardan con los datos de la aplicación y
pisan a las variables de entorno del servidor sin reiniciar.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from ..auth.routes import admin_required
from .. import modelconfig

config_models_bp = Blueprint("config_models", __name__)


@config_models_bp.route("/models", methods=["GET"])
@admin_required
def get_models(_admin):
    """La configuración vigente, con claves enmascaradas y origen de cada campo."""
    return jsonify({"success": True, "data": modelconfig.masked()})


@config_models_bp.route("/models", methods=["PUT"])
@admin_required
def put_models(_admin):
    """Guarda y aplica una configuración nueva.

    En cada campo: texto = valor nuevo, "" = heredar del servidor. Para las
    claves, el valor omitido conserva lo guardado (la interfaz no las reenvía).
    """
    body = request.get_json(silent=True) or {}
    current = modelconfig.load()
    for kind in modelconfig.FIELDS:
        incoming = body.get(kind)
        if incoming is None:
            continue
        if not isinstance(incoming, dict):
            return jsonify({"success": False, "error": f"{kind}: se esperaba un objeto"}), 400
        for field in modelconfig.FIELDS[kind]:
            if field not in incoming:
                continue
            value = incoming[field]
            if field in modelconfig.SECRET_FIELDS and value is None:
                continue  # sin tocar: la interfaz no reenvía claves guardadas
            current[kind][field] = str(value or "").strip()
    try:
        modelconfig.save(current)
    except ValueError as error:
        return jsonify({"success": False, "error": str(error)}), 400
    modelconfig.apply(current)
    return jsonify({"success": True, "data": modelconfig.masked(current)})


@config_models_bp.route("/models/test", methods=["POST"])
@admin_required
def test_models(_admin):
    """Prueba la conexión con un servicio: los valores guardados o unos dados
    (para probar antes de guardar)."""
    body = request.get_json(silent=True) or {}
    kind = body.get("kind")
    overrides = body.get("config") if isinstance(body.get("config"), dict) else None
    result = modelconfig.test_connection(kind, overrides)
    return jsonify({"success": result["ok"], "data": result}), (200 if result["ok"] else 502)
