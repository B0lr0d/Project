"""Journalisation limitée en débit.

Une panne matérielle se répète à chaque cycle d'acquisition. Sans garde-fou,
une sonde débranchée écrirait un message par seconde dans le journal — ce qui
use la carte microSD et rend les journaux illisibles.

``RateLimitedLogger`` n'émet un message identique qu'une fois par fenêtre, puis
résume les occurrences supprimées ::

    Sonde Local eau : capteur absent
    ... (puis, 5 minutes plus tard)
    Sonde Local eau : capteur absent  [312 occurrences supprimées]
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from .timebase import monotonic


@dataclass
class _Entry:
    first_seen: float
    last_emitted: float
    suppressed: int = 0


class RateLimitedLogger:
    """Enveloppe autour d'un ``logging.Logger``.

    Chaque message est identifié par une *clé* stable (par exemple
    ``"temp.local_eau.fault"``) et non par son texte, afin qu'une valeur
    numérique changeante ne contourne pas le dédoublonnage.
    """

    def __init__(self, logger: logging.Logger, window_s: float = 300.0) -> None:
        self._logger = logger
        self._window_s = max(0.0, window_s)
        self._entries: dict[str, _Entry] = {}
        self._lock = threading.Lock()

    def set_window(self, window_s: float) -> None:
        with self._lock:
            self._window_s = max(0.0, window_s)

    def log(self, level: int, key: str, message: str) -> bool:
        """Émet ``message`` si la fenêtre est écoulée. Retourne True si émis."""
        now = monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._entries[key] = _Entry(first_seen=now, last_emitted=now)
                suppressed = 0
            elif now - entry.last_emitted >= self._window_s:
                suppressed = entry.suppressed
                entry.last_emitted = now
                entry.suppressed = 0
            else:
                entry.suppressed += 1
                return False

        if suppressed:
            message = f"{message}  [{suppressed} occurrences supprimées]"
        self._logger.log(level, message)
        return True

    def warning(self, key: str, message: str) -> bool:
        return self.log(logging.WARNING, key, message)

    def error(self, key: str, message: str) -> bool:
        return self.log(logging.ERROR, key, message)

    def info(self, key: str, message: str) -> bool:
        return self.log(logging.INFO, key, message)

    def clear(self, key: str) -> None:
        """Oublie une clé : le prochain message identique sera ré-émis.

        Appelé quand un équipement redevient sain, pour que la panne suivante
        soit signalée immédiatement.
        """
        with self._lock:
            self._entries.pop(key, None)
