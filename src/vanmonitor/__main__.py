"""Point d'entrée : ``python -m vanmonitor [--sim]``."""

from .app import run

if __name__ == "__main__":
    raise SystemExit(run())
