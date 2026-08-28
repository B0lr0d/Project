"""Boucle de contrôle : un tour par seconde, **aucune entrée-sortie matérielle**.

Ce thread ne parle jamais à un capteur. Il lit des valeurs déjà en mémoire,
surveille la santé des threads d'acquisition, et publie un instantané immuable
que l'interface consomme.

C'est cette séparation qui garantit qu'une sonde lente ne retarde ni la logique
ni l'affichage : la lenteur reste confinée dans le thread de la famille
concernée.

À l'étape 2, le tour de boucle se limite à la surveillance et à la publication.
Les étapes suivantes viendront s'insérer ici, dans cet ordre : services
métier (4 à 6), logique de chauffage (7), alertes (8), historique (9).
"""

from __future__ import annotations

import threading
from typing import Callable

from ..models import AcquisitionSnapshot
from ..util.logging_setup import get_logger
from ..util.ratelimit import RateLimitedLogger
from ..util.timebase import monotonic
from .acquisition import AcquisitionService
from .state import StateStore

logger = get_logger("core.control")
limited = RateLimitedLogger(logger)


class ControlWorker(threading.Thread):
    """Le chef d'orchestre. Ne bloque jamais sur du matériel."""

    def __init__(
        self,
        acquisition: AcquisitionService,
        state_store: StateStore,
        *,
        period_s: float | Callable[[], float] = 1.0,
    ) -> None:
        super().__init__(name="control_worker", daemon=True)
        self._acquisition = acquisition
        self._state = state_store
        self._period = period_s if callable(period_s) else (lambda: float(period_s))
        self._stop_event = threading.Event()
        self._ticks = 0

    @property
    def ticks(self) -> int:
        return self._ticks

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:       # pragma: no cover - exercé via les tests d'intégration
        while not self._stop_event.is_set():
            started = monotonic()
            self.tick()
            elapsed = monotonic() - started
            self._stop_event.wait(max(0.0, self._period() - elapsed))

    def tick(self) -> AcquisitionSnapshot:
        """Un tour de boucle. Chaque étape est protégée séparément.

        Une exception dans la surveillance ne doit pas empêcher la publication
        de l'instantané, et inversement : l'écran continue de vivre même quand
        une partie du système est en panne.
        """
        health = []
        try:
            health = self._acquisition.check_workers()
        except Exception as exc:
            limited.error("control.check", f"surveillance des threads : {exc}")

        snapshot = self._acquisition.snapshot(health)

        try:
            self._state.publish(snapshot)
        except Exception as exc:
            limited.error("control.publish", f"publication de l'instantané : {exc}")

        self._ticks += 1
        return snapshot
