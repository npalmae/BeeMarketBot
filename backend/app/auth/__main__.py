"""Punto de entrada: ``python -m app.auth <comando>``."""

import sys

from .cli import main

sys.exit(main())
