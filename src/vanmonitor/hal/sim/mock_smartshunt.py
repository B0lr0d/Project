"""SmartShunt simulé.

Reproduit le comportement d'une liaison VE.Direct filaire : il faut ouvrir la
liaison avant de lire, elle peut se couper à tout moment, et une coupure ne se
manifeste qu'à la lecture suivante.
"""

from __future__ import annotations

from ...constants import Status
from ...models import BatteryReading
from ...util.timebase import monotonic
from ..interfaces import LinkError, SmartShuntInterface
from .sim_state import FaultMode, SimState, apply_fault_mode


class MockSmartShuntInterface(SmartShuntInterface):
    """SmartShunt simulé, joignable ou non selon l'état du monde simulé."""

    def __init__(self, sim_state: SimState, *, timeout_s: float = 2.0) -> None:
        self._sim = sim_state
        self._timeout_s = timeout_s
        self._connected = False

    def connect(self) -> None:
        if self._sim.battery_fault() is FaultMode.ABSENT:
            raise LinkError("SmartShunt : liaison VE.Direct indisponible")
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._sim.battery_fault() is not FaultMode.ABSENT

    def read(self) -> BatteryReading:
        if not self._connected:
            raise LinkError("SmartShunt : liaison non ouverte")

        mode = self._sim.battery_fault()
        try:
            apply_fault_mode(
                mode,
                self._timeout_s,
                still_faulty=lambda: self._sim.battery_fault() is mode,
                error=LinkError,
                label="SmartShunt",
                scale=self._sim.time_scale,
            )
        except Exception:
            # Une liaison série tombée se referme : la reconnexion devra être
            # explicite, exactement comme avec le câble réel.
            self._connected = False
            raise

        battery = self._sim.battery()
        return BatteryReading(
            soc_percent=battery.soc_percent,
            voltage_v=battery.voltage_v,
            current_a=battery.current_a,
            power_w=battery.power_w,
            consumed_ah=battery.consumed_ah,
            time_to_go_min=(battery.time_to_go_min
                            if battery.time_to_go_available else None),
            status=Status.OK,
            updated_at=monotonic(),
        )

    def __repr__(self) -> str:      # pragma: no cover - confort de débogage
        state = "connecté" if self.is_connected() else "déconnecté"
        return f"<MockSmartShuntInterface {state}>"
