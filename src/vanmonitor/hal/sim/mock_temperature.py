"""Sonde de température simulée."""

from __future__ import annotations

import random
import time

from ...constants import ZoneId
from ..interfaces import SensorError, TemperatureSensor
from .sim_state import FaultMode, SIM_SENSOR_IDS, SimState, apply_fault_mode


class MockTemperatureSensor(TemperatureSensor):
    """Reproduit une DS18B20 : identifiant unique, lecture lente, pannes.

    Le léger bruit ajouté à la valeur n'est pas cosmétique : il garantit que
    l'affichage sait gérer une valeur qui bouge en permanence, et que
    l'arrondi choisi à l'écran ne fait pas danser les chiffres.
    """

    #: Amplitude du bruit de mesure, en degrés.
    NOISE_C = 0.05

    def __init__(
        self,
        zone: ZoneId,
        sim_state: SimState,
        *,
        timeout_s: float = 3.0,
        conversion_s: float = 0.0,
    ) -> None:
        self._zone = zone
        self._sim = sim_state
        self._timeout_s = timeout_s
        # Temps de conversion simulé : une vraie DS18B20 met environ 750 ms.
        # Laissé à zéro par défaut pour ne pas ralentir les tests.
        self._conversion_s = conversion_s
        self._random = random.Random(hash(zone.value) & 0xFFFF)

    def sensor_id(self) -> str:
        return SIM_SENSOR_IDS[self._zone]

    def is_present(self) -> bool:
        return self._sim.temperature_fault(self._zone) is not FaultMode.ABSENT

    def read_celsius(self) -> float:
        mode = self._sim.temperature_fault(self._zone)
        apply_fault_mode(
            mode,
            self._timeout_s,
            still_faulty=lambda: self._sim.temperature_fault(self._zone) is mode,
            error=SensorError,
            label=f"sonde {self._zone.value}",
            scale=self._sim.time_scale,
        )

        if self._conversion_s > 0:
            time.sleep(self._conversion_s * self._sim.time_scale)

        value = self._sim.temperature(self._zone)
        return value + self._random.uniform(-self.NOISE_C, self.NOISE_C)

    def __repr__(self) -> str:      # pragma: no cover - confort de débogage
        return f"<MockTemperatureSensor {self._zone.value} {self.sensor_id()}>"
