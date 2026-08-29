"""Veille de l'affichage.

**Seul l'écran s'éteint.** Le Raspberry reste actif, les threads d'acquisition
tournent, le SmartShunt est lu, le chauffage régule et les alertes sont
évaluées. Aucun worker n'est suspendu, aucun thread n'est arrêté, et Linux ne
passe jamais en veille.

Le compte à rebours part de la dernière interaction tactile. Le premier toucher
qui suit une extinction est **consommé par le réveil** : il ne doit déclencher
aucun bouton, ne changer aucun réglage, ne commander aucun clapet. Cette
consommation est réalisée dans l'interface (``ui/wake_guard.py``) ; ce module
n'en connaît que le résultat.

L'extinction et le rallumage passent par un thread dédié : ce sont des
entrées-sorties (une commande externe, une écriture sysfs) et elles n'ont donc
rien à faire dans le thread graphique. Le réveil reste immédiat parce que ce
thread attend un événement, pas une échéance.
"""

from __future__ import annotations

import threading

from ..config import ConfigStore
from ..models import DisplayStatus
from ..util.logging_setup import get_logger
from ..util.ratelimit import RateLimitedLogger
from ..util.timebase import monotonic
from ..hal.interfaces import DisplayPower, HardwareError

logger = get_logger("core.display")
limited = RateLimitedLogger(logger)

#: Délais proposés dans les Paramètres, en secondes. ``0`` désactive la veille.
SLEEP_DELAYS_S = (0, 60, 300, 600, 1800)


class DisplayController:
    """Décide quand éteindre l'écran, et le rallume au premier toucher."""

    def __init__(self, power: DisplayPower, config: ConfigStore) -> None:
        self._power = power
        self._config = config
        self._lock = threading.Lock()
        self._last_activity = monotonic()
        self._asleep = False
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    # Réglages
    # ------------------------------------------------------------------
    @property
    def enabled(self) -> bool:
        return bool(self._config.get("display.sleep_enabled", True))

    @property
    def delay_s(self) -> float:
        return max(0.0, float(self._config.get("display.sleep_delay_s", 300)))

    @property
    def is_asleep(self) -> bool:
        with self._lock:
            return self._asleep

    @property
    def available(self) -> bool:
        return self._power.is_available()

    def status(self, now: float | None = None) -> DisplayStatus:
        instant = monotonic() if now is None else now
        with self._lock:
            idle = max(0.0, instant - self._last_activity)
            return DisplayStatus(
                asleep=self._asleep,
                enabled=self.enabled,
                available=self._power.is_available(),
                delay_s=self.delay_s,
                idle_s=idle,
                method=self._power.describe(),
                last_error=self._last_error,
            )

    # ------------------------------------------------------------------
    # Activité
    # ------------------------------------------------------------------
    def note_activity(self, now: float | None = None) -> None:
        """Remet le compteur d'inactivité à zéro. Appelé à chaque interaction."""
        with self._lock:
            self._last_activity = monotonic() if now is None else now

    def idle_seconds(self, now: float | None = None) -> float:
        instant = monotonic() if now is None else now
        with self._lock:
            return max(0.0, instant - self._last_activity)

    # ------------------------------------------------------------------
    # Décision
    # ------------------------------------------------------------------
    def tick(self, now: float | None = None) -> bool:
        """Applique la règle de veille. Retourne True si l'état a changé.

        Ne rallume jamais de lui-même : le réveil est toujours provoqué par un
        toucher, ou par la désactivation du réglage.
        """
        instant = monotonic() if now is None else now

        if not self.enabled:
            # La veille vient d'être désactivée alors que l'écran dormait.
            return self.wake(instant) if self.is_asleep else False

        with self._lock:
            if self._asleep:
                return False
            idle = instant - self._last_activity
            delay = self.delay_s
            due = delay > 0 and idle >= delay
        if not due:
            return False
        return self._sleep(instant)

    def wake(self, now: float | None = None) -> bool:
        """Rallume l'écran et repart pour un tour. Retourne True si changement."""
        instant = monotonic() if now is None else now
        with self._lock:
            self._last_activity = instant
            if not self._asleep:
                return False

        try:
            self._power.wake()
        except HardwareError as exc:
            # L'écran refuse de se rallumer : on ne le laisse surtout pas
            # marqué endormi, sinon le prochain toucher serait avalé pour rien.
            with self._lock:
                self._asleep = False
                self._last_error = str(exc)
            limited.error("display.wake", f"rallumage de l'écran : {exc}")
            return True

        with self._lock:
            self._asleep = False
            self._last_error = None
        logger.info("écran rallumé")
        return True

    def _sleep(self, now: float) -> bool:
        try:
            self._power.sleep()
        except HardwareError as exc:
            # L'extinction a échoué : l'écran reste allumé et le programme le
            # dit. Se croire endormi ferait avaler le prochain toucher.
            with self._lock:
                self._asleep = False
                self._last_error = str(exc)
            limited.warning("display.sleep", f"mise en veille impossible : {exc}")
            return False

        with self._lock:
            self._asleep = True
            self._last_error = None
        logger.info("écran mis en veille après %.0f s d'inactivité", self.delay_s)
        return True

    def shutdown(self) -> None:
        """Rallume l'écran à l'arrêt du programme : on ne laisse pas un noir."""
        if self.is_asleep:
            try:
                self._power.wake()
            except HardwareError:
                pass
            with self._lock:
                self._asleep = False


class DisplayWorker(threading.Thread):
    """Applique les décisions de veille hors du thread graphique.

    Le fil attend un événement plutôt qu'une échéance : un réveil demandé par
    un doigt est traité immédiatement, tandis que l'endormissement se vérifie à
    intervalle régulier — il n'a aucune raison d'être précis à la seconde.
    """

    def __init__(self, controller: DisplayController, *, period_s: float = 1.0) -> None:
        super().__init__(name="display_worker", daemon=True)
        self._controller = controller
        self._period_s = max(0.05, period_s)
        self._wake_requested = threading.Event()
        self._stop_event = threading.Event()

    def request_wake(self) -> None:
        """Demande un réveil immédiat. Appelable depuis le thread graphique."""
        self._wake_requested.set()

    def request_stop(self) -> None:
        self._stop_event.set()
        self._wake_requested.set()

    def run(self) -> None:      # pragma: no cover - exercé par les tests d'intégration
        while not self._stop_event.is_set():
            if self._wake_requested.is_set():
                self._wake_requested.clear()
                if not self._stop_event.is_set():
                    self._safe(self._controller.wake)
            else:
                self._safe(self._controller.tick)
            self._wake_requested.wait(self._period_s)

    @staticmethod
    def _safe(action) -> None:
        try:
            action()
        except Exception as exc:        # la veille ne fait jamais tomber le reste
            limited.error("display.worker", f"veille d'écran : {exc}")
