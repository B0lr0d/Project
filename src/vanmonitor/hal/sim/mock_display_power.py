"""Affichage simulé : la veille se vérifie sans éteindre quoi que ce soit."""

from __future__ import annotations

import threading

from ...constants import DisplayState
from ..interfaces import DisplayPower, HardwareError


class MockDisplayPower(DisplayPower):
    """Écran simulé, capable de refuser de s'éteindre.

    Le mode ``failing`` reproduit une méthode d'extinction qui ne fonctionne
    pas sur la machine cible : la veille doit alors le signaler et laisser
    l'écran allumé, pas se croire endormie.
    """

    def __init__(self, *, available: bool = True, failing: bool = False) -> None:
        self._lock = threading.Lock()
        self._state = DisplayState.ON
        self._available = available
        self._failing = failing
        self.sleep_calls = 0
        self.wake_calls = 0

    # ------------------------------------------------------------------
    def sleep(self) -> None:
        with self._lock:
            self.sleep_calls += 1
            if self._failing:
                raise HardwareError("écran simulé : extinction refusée")
            self._state = DisplayState.OFF

    def wake(self) -> None:
        with self._lock:
            self.wake_calls += 1
            if self._failing:
                raise HardwareError("écran simulé : rallumage refusé")
            self._state = DisplayState.ON

    def state(self) -> DisplayState:
        with self._lock:
            return self._state

    def is_available(self) -> bool:
        return self._available

    def describe(self) -> str:
        return "écran simulé"

    # ------------------------------------------------------------------
    def set_failing(self, failing: bool) -> None:
        """Panne provoquée depuis le panneau de simulation."""
        with self._lock:
            self._failing = bool(failing)

    @property
    def is_off(self) -> bool:
        return self.state() is DisplayState.OFF
