"""Cuentas de usuario en una base SQLite propia.

La base vive junto a los datos de la aplicación (por defecto en
``uploads/auth/users.db``), así que se conserva entre reinicios y entra en las
copias de seguridad. Cada operación abre y cierra su conexión: es seguro desde
varios hilos y no guarda estado entre peticiones.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .passwords import username_key
from .roles import ADMIN, is_valid_role

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    disabled INTEGER NOT NULL DEFAULT 0,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_UPDATABLE = {"role", "disabled", "password_hash", "must_change_password"}


class UsernameTaken(Exception):
    """Ya existe una cuenta con ese nombre de usuario."""


class UserNotFound(Exception):
    """No existe una cuenta con ese identificador."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class User:
    """Una cuenta. El hash de la contraseña nunca aparece en su representación."""

    id: int
    username: str
    role: str
    disabled: bool
    must_change_password: bool
    created_at: str
    password_hash: str = field(default="", repr=False)

    @property
    def is_active(self) -> bool:
        return not self.disabled

    def to_dict(self) -> dict[str, Any]:
        """Lo que ve la interfaz: nada del hash."""

        return {
            "id": self.id,
            "username": self.username,
            "role": self.role,
            "disabled": self.disabled,
            "must_change_password": self.must_change_password,
            "created_at": self.created_at,
        }


def _user_from_row(row: sqlite3.Row) -> User:
    return User(
        id=int(row["id"]),
        username=row["username"],
        role=row["role"],
        disabled=bool(row["disabled"]),
        must_change_password=bool(row["must_change_password"]),
        created_at=row["created_at"],
        password_hash=row["password_hash"],
    )


class UserStore:
    """Guarda y consulta cuentas."""

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._write() as connection:
            connection.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        # Un corte de energía no deja una base a medias escrita.
        connection.execute("PRAGMA journal_mode=WAL")
        # Si el archivo se perdió con la aplicación andando, la base vuelve a
        # crearse (sin cuentas) en vez de fallar cada petición.
        present = connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()
        if not present:
            connection.executescript(SCHEMA)
        return connection

    @contextmanager
    def _write(self):
        """Contexto de escritura: espera al candado y confirma al salir."""

        with self._lock, closing(self._connect()) as connection:
            with connection:
                yield connection

    @contextmanager
    def _read(self):
        with closing(self._connect()) as connection:
            yield connection

    # ------------------------------------------------------------ consultas
    def count(self) -> int:
        with self._read() as connection:
            return int(connection.execute("SELECT count(*) FROM users").fetchone()[0])

    def get(self, user_id: int) -> User | None:
        with self._read() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (int(user_id),)).fetchone()
        return _user_from_row(row) if row else None

    def find(self, username: str) -> User | None:
        """Cuenta por nombre de usuario, sin distinguir mayúsculas."""

        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM users WHERE username_key = ?", (username_key(username),)
            ).fetchone()
        return _user_from_row(row) if row else None

    def list(self) -> list[User]:
        with self._read() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY username_key").fetchall()
        return [_user_from_row(row) for row in rows]

    def active_admins(self) -> int:
        with self._read() as connection:
            row = connection.execute(
                "SELECT count(*) FROM users WHERE role = ? AND disabled = 0", (ADMIN,)
            ).fetchone()
        return int(row[0])

    # ------------------------------------------------------------ cambios
    def create(
        self, username: str, password_hash: str, role: str, *, must_change_password: bool
    ) -> User:
        if not is_valid_role(role):
            raise ValueError(f"Nivel de acceso no válido: {role!r}")
        now = _now()
        with self._write() as connection:
            try:
                cursor = connection.execute(
                    """
                    INSERT INTO users
                        (username, username_key, password_hash, role, disabled,
                         must_change_password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (username.strip(), username_key(username), password_hash, role, int(must_change_password), now, now),
                )
            except sqlite3.IntegrityError as error:
                raise UsernameTaken(username) from error
        return self.get(int(cursor.lastrowid))  # type: ignore[return-value] - recién creado

    def update(self, user_id: int, **changes: Any) -> User:
        unknown = set(changes) - _UPDATABLE
        if unknown:
            raise ValueError(f"Campos no modificables: {sorted(unknown)}")
        if "role" in changes and not is_valid_role(changes["role"]):
            raise ValueError(f"Nivel de acceso no válido: {changes['role']!r}")
        assignments = ", ".join(f"{name} = ?" for name in changes)
        values = [int(value) if name in ("disabled", "must_change_password") else value for name, value in changes.items()]
        with self._write() as connection:
            cursor = connection.execute(
                f"UPDATE users SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, _now(), int(user_id)),
            )
            if cursor.rowcount != 1:
                raise UserNotFound(user_id)
        return self.get(int(user_id))  # type: ignore[return-value] - acaba de existir
