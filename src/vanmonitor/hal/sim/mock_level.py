"""Capteur de niveau simulé.

Rend une valeur **brute** sans unité, exactement comme le fera le matériel
réel : c'est la calibration multipoints (étape 5) qui la transformera en litres
ou en pourcentage.
"""

from __future__ import annotations

import random

from ...constants import TankId
from ..interfaces import LevelSensor, SensorError
from .sim_state import FaultMode, SimState, apply_fault_mode


class MockLevelSensor(LevelSensor):
    """Capteur de niveau simulé, avec bruit et ballottement optionnel.

    Le ballottement reproduit le risque R-10 : en roulant, la surface du
    liquide bouge et la mesure saute. Le filtrage devra l'absorber.
    """

    NOISE_RAW = 0.002

    def __init__(
        self,
        tank: TankId,
        sim_state: SimState,
        *,
        timeout_s: float = 1.0,
        sloshing: float = 0.0,
    ) -> None:
        self._tank = tank
        self._sim = sim_state
        self._timeout_s = timeout_s
        self._sloshing = max(0.0, sloshing)
        self._random = random.Random(hash(tank.value) & 0xFFFF)

    def is_present(self) -> bool:
        return self._sim.level_fault(self._tank) is not FaultMode.ABSENT

    def read_raw(self) -> float:
        mode = self._sim.level_fault(self._tank)
        apply_fault_mode(
            mode,
            self._timeout_s,
            still_faulty=lambda: self._sim.level_fault(self._tank) is mode,
            error=SensorError,
            label=f"niveau {self._tank.value}",
            scale=self._sim.time_scale,
        )

        value = self._sim.level(self._tank)
        noise = self._random.uniform(-self.NOISE_RAW, self.NOISE_RAW)
        if self._sloshing:
            noise += self._random.uniform(-self._sloshing, self._sloshing)
        return value + noise

    def set_sloshing(self, amplitude: float) -> None:
        self._sloshing = max(0.0, amplitude)

    def __repr__(self) -> str:      # pragma: no cover - confort de débogage
        return f"<MockLevelSensor {self._tank.value}>"
