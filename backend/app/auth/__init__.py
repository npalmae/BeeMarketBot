"""Inicio de sesión y niveles de acceso.

Toda la API exige una sesión iniciada, salvo el estado de la sesión y el
inicio de sesión mismo. Hay tres niveles:

- ``admin``: todo, y además gestiona los usuarios.
- ``editor``: crea y ejecuta simulaciones y genera informes.
- ``viewer``: solo consulta.

No hay registro público ni contraseñas por defecto: el primer administrador se
crea con ``python -m app.auth create-user`` o con ``ADMIN_USERNAME`` y
``ADMIN_PASSWORD`` en el entorno.

Módulos:

- ``roles``: niveles y permisos.
- ``passwords``: reglas de contraseña y hash.
- ``store``: cuentas de usuario en SQLite.
- ``service``: operaciones y reglas de negocio.
- ``routes``: la API bajo ``/api/auth``.
- ``guard``: protección de toda la API y creación del primer administrador.
- ``sessions``: la sesión firmada y el token anti-CSRF.
- ``throttle``: límite de intentos fallidos de inicio de sesión.
- ``cli``: administración de cuentas desde la línea de comandos.
"""

from .roles import ADMIN, EDITOR, ROLES, VIEWER

__all__ = ["ADMIN", "EDITOR", "ROLES", "VIEWER"]
