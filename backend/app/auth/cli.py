"""Administración de cuentas desde la línea de comandos.

Se ejecuta dentro del contenedor o del entorno del backend::

    python -m app.auth create-user --username ana --role editor
    python -m app.auth create-user --username ana --role admin --generate-password
    python -m app.auth list-users
    python -m app.auth set-password --username ana
    python -m app.auth set-role --username ana --role viewer
    python -m app.auth disable --username ana
    python -m app.auth enable --username ana

La contraseña se pide dos veces sin mostrarla. Para automatizaciones se lee de
la entrada estándar con ``--password-stdin`` (una sola línea), o se genera con
``--generate-password`` y se muestra una sola vez. Nunca se pasa como argumento,
donde quedaría a la vista en la lista de procesos.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from typing import Any

from ..config import Config
from ..utils.locale import set_locale, t
from .passwords import generate_password
from .roles import ROLES
from .service import AuthError, AuthService, NotFound
from .store import UserStore

PASSWORD_STDIN = "--password-stdin"
GENERATE_PASSWORD = "--generate-password"


def _read_password(arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> str | None:
    """La contraseña elegida, o None si hay que generarla."""

    if getattr(arguments, "generate_password", False):
        return None
    if getattr(arguments, "password_stdin", False):
        return sys.stdin.readline().rstrip("\r\n")
    if not sys.stdin.isatty():
        parser.error(f"hace falta {PASSWORD_STDIN} o {GENERATE_PASSWORD} cuando no hay terminal")
    first = getpass.getpass("Contraseña: ")
    second = getpass.getpass("Repite la contraseña: ")
    if first != second:
        parser.error("las contraseñas no coinciden")
    return first


def _password_options(command: argparse.ArgumentParser) -> None:
    """Las dos formas de dar la contraseña sin escribirla en el comando."""

    group = command.add_mutually_exclusive_group()
    group.add_argument(PASSWORD_STDIN, dest="password_stdin", action="store_true")
    group.add_argument(
        GENERATE_PASSWORD,
        dest="generate_password",
        action="store_true",
        help="genera una contraseña y la muestra una sola vez",
    )


def _show_generated(arguments: argparse.Namespace, password: str) -> None:
    if getattr(arguments, "generate_password", False):
        print(f"Contraseña generada, se muestra solo esta vez: {password}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.auth", description="Administra las cuentas de BeeMarketBot.")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create-user", help="crea una cuenta; deberá cambiar la contraseña al entrar")
    create.add_argument("--username", required=True)
    create.add_argument("--role", default="viewer", choices=ROLES)
    _password_options(create)

    commands.add_parser("list-users", help="lista las cuentas")

    password = commands.add_parser("set-password", help="fija una contraseña nueva; deberá cambiarla al entrar")
    password.add_argument("--username", required=True)
    _password_options(password)

    role = commands.add_parser("set-role", help="cambia el nivel de acceso")
    role.add_argument("--username", required=True)
    role.add_argument("--role", required=True, choices=ROLES)

    for name, help_text in (("disable", "impide que la cuenta entre"), ("enable", "vuelve a permitir que la cuenta entre")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--username", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    set_locale(os.environ.get("APP_LANG", "es"))
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    service = AuthService(UserStore(Config.AUTH_DB_PATH))

    try:
        return _run(service, arguments, parser)
    except AuthError as error:
        print(f"Error: {t(error.key, **error.params)}", file=sys.stderr)
        return 1


def _by_username(service: AuthService, username: str) -> Any:
    user = service.store.find(username)
    if user is None:
        raise NotFound(username)
    return user


def _run(service: AuthService, arguments: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    command = arguments.command

    if command == "create-user":
        chosen = _read_password(arguments, parser)
        password = generate_password() if chosen is None else chosen
        user = service.create_user(arguments.username, password, arguments.role)
        print(f"Cuenta creada: {user.username} ({user.role}). Deberá cambiar la contraseña al entrar.")
        _show_generated(arguments, password)
    elif command == "list-users":
        users = service.store.list()
        if not users:
            print("No hay cuentas. Cree el primer administrador con: python -m app.auth create-user --username <nombre> --role admin")
        for user in users:
            state = "deshabilitada" if user.disabled else "activa"
            pending = ", debe cambiar la contraseña" if user.must_change_password else ""
            print(f"{user.id:>4}  {user.username:<24} {user.role:<8} {state}{pending}")
    elif command == "set-password":
        user = _by_username(service, arguments.username)
        chosen = _read_password(arguments, parser)
        password = generate_password() if chosen is None else chosen
        service.set_password(user.id, password, must_change=True)
        print(f"Contraseña cambiada: {user.username}. Deberá cambiarla al entrar.")
        _show_generated(arguments, password)
    elif command == "set-role":
        user = service.set_role(_by_username(service, arguments.username).id, arguments.role)
        print(f"Nivel de acceso cambiado: {user.username} ({user.role})")
    else:
        user = _by_username(service, arguments.username)
        service.set_disabled(user.id, command == "disable")
        print(f"{user.username}: {'deshabilitada' if command == 'disable' else 'habilitada'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
