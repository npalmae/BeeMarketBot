"""Límite de intentos fallidos de inicio de sesión.

Va en memoria y por proceso: frena la prueba de contraseñas por fuerza bruta sin
depender de nada externo.

Se cuenta por dirección de origen y nombre de usuario, así que los intentos de
un atacante desde otro equipo no bloquean al usuario legítimo. Además se cuenta
por dirección, para acotar el sondeo de nombres de usuario.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable, Sequence

Address = str


class LoginThrottle:
    def __init__(
        self,
        max_failures: int = 10,
        window_seconds: float = 600.0,
        per_address: int = 200,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = float(window_seconds)
        self.per_address = max(self.max_failures, int(per_address))
        self._clock = clock
        self._lock = threading.Lock()
        self._failures: dict[object, deque[float]] = {}

    def _recent(self, key: object, now: float) -> Sequence[float]:
        """Instantes de los fallos de una clave que siguen dentro de la ventana.

        Consultar no crea la entrada: la memoria solo crece cuando se registra un fallo.
        """

        attempts = self._failures.get(key)
        if attempts is None:
            return ()
        while attempts and now - attempts[0] > self.window_seconds:
            attempts.popleft()
        return attempts

    def _blocked_for(self, key: object, limit: int, now: float) -> int:
        attempts = self._recent(key, now)
        if len(attempts) < limit:
            return 0
        # El bloqueo se levanta cuando el más antiguo de los fallos que lo causan sale de la ventana.
        first_counted = attempts[-limit]
        return max(1, int(self.window_seconds - (now - first_counted)) + 1)

    def retry_after(self, address: Address, username: str) -> int:
        """Segundos que hay que esperar antes de volver a intentar; 0 si no hay bloqueo."""

        now = self._clock()
        with self._lock:
            return max(
                self._blocked_for((address, username), self.max_failures, now),
                self._blocked_for(address, self.per_address, now),
            )

    def record_failure(self, address: Address, username: str) -> None:
        now = self._clock()
        with self._lock:
            for key in ((address, username), address):
                attempts = self._recent(key, now) or self._failures.setdefault(key, deque())
                attempts.append(now)

    def reset(self, address: Address, username: str) -> None:
        """Tras un inicio de sesión correcto, se olvidan sus fallos."""

        with self._lock:
            self._failures.pop((address, username), None)
