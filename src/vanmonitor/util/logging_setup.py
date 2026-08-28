"""Configuration de la journalisation.

Sur le Raspberry, les journaux partent vers la sortie d'erreur, que systemd
capture dans le journal système. Aucun fichier de log n'est écrit par
l'application : c'est un choix délibéré pour ne pas user la carte microSD.
"""

from __future__ import annotations

import logging
import sys

from ..constants import APP_NAME

_CONFIGURED = False

_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)-28s  %(message)s"
_DATEFMT = "%H:%M:%S"


def setup_logging(level: str = "INFO") -> logging.Logger:
    """Configure la racine une seule fois et retourne le logger applicatif."""
    global _CONFIGURED

    numeric = getattr(logging, str(level).upper(), logging.INFO)
    if not isinstance(numeric, int):
        numeric = logging.INFO

    root = logging.getLogger()
    if not _CONFIGURED:
        handler = logging.StreamHandler(stream=sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
        root.addHandler(handler)
        _CONFIGURED = True
    root.setLevel(numeric)

    return logging.getLogger(APP_NAME)


def get_logger(suffix: str) -> logging.Logger:
    """Logger nommé ``vanmonitor.<suffix>``."""
    return logging.getLogger(f"{APP_NAME}.{suffix}")
