"""Niveles de acceso de los usuarios."""

from __future__ import annotations

VIEWER = "viewer"
EDITOR = "editor"
ADMIN = "admin"

# De menor a mayor nivel.
ROLES = (VIEWER, EDITOR, ADMIN)

# Niveles que pueden crear, modificar, ejecutar y borrar.
WRITE_ROLES = frozenset({EDITOR, ADMIN})


def is_valid_role(role: object) -> bool:
    return role in ROLES
