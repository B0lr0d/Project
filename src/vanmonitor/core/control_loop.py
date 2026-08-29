"""Boucle de contrôle : un tour par seconde, **aucune entrée-sortie matérielle**.

Ce thread ne parle jamais à un capteur. Il lit des valeurs déjà en mémoire,
surveille la santé des threads d'acquisition, assemble l'instantané du système
et le publie. L'écran n'a plus qu'à le dessiner.

C'est cette séparation qui garantit qu'une sonde lente ne retarde ni la logique
ni l'affichage : la lenteur reste confinée dans le thread de sa famille.

Reste à insérer ici : la régulation automatique du chauffage (étape 7) et
l'enregistrement de l'historique (étape 9).
"""

from __future__ import annotations

import threading
from typing import Callable

from ..models import SystemSnapshot
from ..util.logging_setup import get_logger
from ..util.ratelimit import RateLimitedLogger
from ..util.timebase import monotonic
from .acquisition import AcquisitionService
from .alerts import AlertEngine
from .display import DisplayController
from .services import SnapshotBuilder
from .state import StateStore

logger = get_logger("core.control")
limited = RateLimitedLogger(logger)


class ControlWorker(threading.Thread):
    """Le chef d'orchestre. Ne bloque jamais sur du matériel."""

    def __init__(
        self,
        acquisition: AcquisitionService,
        state_store: StateStore,
        builder: SnapshotBuilder,
        alerts: AlertEngine,
        *,
        period_s: float | Callable[[], float] = 1.0,
        display: DisplayController | None = None,
    ) -> None:
        super().__init__(name="control_worker", daemon=True)
        self._acquisition = acquisition
        self._state = state_store
        self._builder = builder
        self._alerts = alerts
        # La veille n'est ici que pour être publiée : cette boucle ne décide
        # jamais d'éteindre quoi que ce soit, et ne s'arrête pas quand l'écran
        # est noir.
        self._display = display
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

    def tick(self) -> SystemSnapshot:
        """Un tour de boucle. Chaque étape est protégée séparément.

        Une exception dans la surveillance ne doit pas empêcher la publication
        de l'instantané, et inversement : l'écran continue de vivre même quand
        une partie du système est en panne.
        """
        health: list = []
        try:
            health = self._acquisition.check_workers()
        except Exception as exc:
            limited.error("control.check", f"surveillance des threads : {exc}")

        acquisition = self._acquisition.snapshot(health)
        snapshot = self._builder.build(acquisition)

        try:
            alerts = self._alerts.evaluate(
                battery=snapshot.battery,
                tanks=snapshot.tanks,
                temperatures=snapshot.temperatures,
                circuits=snapshot.circuits,
                worker_health=acquisition.workers,
                now=acquisition.timestamp,
            )
        except Exception as exc:
            limited.error("control.alerts", f"évaluation des alertes : {exc}")
            alerts = ()

        display = None
        if self._display is not None:
            try:
                display = self._display.status()
            except Exception as exc:
                limited.error("control.display", f"état de la veille : {exc}")

        snapshot = SystemSnapshot(
            timestamp=snapshot.timestamp,
            temperatures=snapshot.temperatures,
            tanks=snapshot.tanks,
            battery=snapshot.battery,
            circuits=snapshot.circuits,
            alerts=alerts,
            available_sensor_ids=snapshot.available_sensor_ids,
            sensor_temperatures=snapshot.sensor_temperatures,
            display=display,
            simulation=snapshot.simulation,
        )

        try:
            self._state.publish(snapshot)
        except Exception as exc:
            limited.error("control.publish", f"publication de l'instantané : {exc}")

        self._ticks += 1
        return snapshot
