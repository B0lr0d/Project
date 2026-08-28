"""La couche d'acquisition complète, montée avec le matériel simulé.

Ce sont les tests qui répondent à l'exigence de fiabilité : une panne d'un
équipement ne doit jamais faire disparaître les autres.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vanmonitor.config import ConfigStore
from vanmonitor.constants import (
    CircuitId,
    ConfirmedState,
    Status,
    TankId,
    ValveCommand,
    ZoneId,
)
from vanmonitor.core.acquisition import AcquisitionService
from vanmonitor.core.alerts import AlertEngine
from vanmonitor.core.commands import CommandBus, ManualValveCommand
from vanmonitor.core.control_loop import ControlWorker
from vanmonitor.core.services import SnapshotBuilder
from vanmonitor.core.state import StateStore
from vanmonitor.hal.factory import build_hal
from vanmonitor.hal.sim.sim_state import FaultMode
from vanmonitor.ui.snapshot_text import format_snapshot


def _wait_until(predicate, timeout_s: float = 4.0, step_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


def _valve(acquisition: AcquisitionService, circuit: CircuitId):
    """Dernière observation d'un clapet, ou ``None`` s'il n'a pas encore été lu."""
    sample = acquisition.snapshot().valves.get(circuit)
    return None if sample is None else sample.value


@pytest.fixture()
def rig(tmp_path: Path):
    """Une installation simulée complète, avec des périodes très courtes."""
    config = ConfigStore(tmp_path / "config.json", debounce_s=0.0)
    config.load()
    config.set("temperatures.poll_period_s", 1)
    config.set("temperatures.read_timeout_s", 0.5)
    config.set("tanks.poll_period_s", 1)
    config.set("battery.poll_period_s", 1)
    config.set("battery.reconnect_backoff_s", [0.05])

    hal = build_hal(config, simulation=True)
    hal.sim_state.set_time_scale(0.01)
    bus = CommandBus()
    acquisition = AcquisitionService(hal, config, bus)
    acquisition.start()
    try:
        yield config, hal, bus, acquisition
    finally:
        acquisition.stop(timeout_s=1.0)


# ---------------------------------------------------------------------------

def test_everything_is_acquired_in_simulation(rig) -> None:
    _config, _hal, _bus, acquisition = rig

    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CABINE].ok)
    snapshot = acquisition.snapshot()

    assert all(snapshot.temperatures[zone].ok for zone in ZoneId)
    assert all(snapshot.levels[tank].ok for tank in TankId)
    assert snapshot.battery.ok
    assert snapshot.simulation is True
    assert snapshot.battery.value.soc_percent == pytest.approx(87.0)


def test_one_broken_sensor_does_not_hide_the_others(rig) -> None:
    """L'exigence de fiabilité, au cœur : une panne reste locale."""
    _config, hal, _bus, acquisition = rig
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CABINE].ok)

    hal.sim_state.set_temperature_fault(ZoneId.LOCAL_EAU, FaultMode.ERROR)
    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.LOCAL_EAU].status is Status.FAULT
    )

    snapshot = acquisition.snapshot()
    assert snapshot.temperatures[ZoneId.LOCAL_EAU].status is Status.FAULT
    for zone in (ZoneId.CABINE, ZoneId.COFFRE, ZoneId.CELLULE, ZoneId.LOCAL_BATTERIE):
        assert snapshot.temperatures[zone].ok, f"{zone} ne devrait pas être affectée"
    assert snapshot.battery.ok
    assert all(snapshot.levels[tank].ok for tank in TankId)


def test_unplugged_sensor_reads_absent_not_faulty(rig) -> None:
    """Une sonde débranchée n'est pas un capteur en défaut : elle n'est plus là.

    La distinction se voit à l'écran : ``--`` d'un côté, « Erreur capteur » de
    l'autre.
    """
    _config, hal, _bus, acquisition = rig
    hal.sim_state.set_temperature_fault(ZoneId.COFFRE, FaultMode.ABSENT)
    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.COFFRE].status is Status.ABSENT
    )

    hal.sim_state.set_temperature_fault(ZoneId.CELLULE, FaultMode.ERROR)
    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.CELLULE].status is Status.FAULT
    )


def test_sensor_recovers_on_its_own(rig) -> None:
    _config, hal, _bus, acquisition = rig
    hal.sim_state.set_temperature_fault(ZoneId.COFFRE, FaultMode.ABSENT)
    assert _wait_until(
        lambda: not acquisition.snapshot().temperatures[ZoneId.COFFRE].ok
    )

    hal.sim_state.set_temperature_fault(ZoneId.COFFRE, FaultMode.OK)
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.COFFRE].ok)


def test_out_of_range_reading_is_rejected(rig) -> None:
    """Un bus 1-Wire bruité peut rendre n'importe quoi : c'est refusé."""
    _config, hal, _bus, acquisition = rig
    hal.sim_state.set_temperature(ZoneId.CELLULE, 900.0)
    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.CELLULE].status is Status.FAULT
    )
    sample = acquisition.snapshot().temperatures[ZoneId.CELLULE]
    assert "hors plage" in (sample.reason or "")


def test_smartshunt_loss_and_reconnection(rig) -> None:
    _config, hal, _bus, acquisition = rig
    assert _wait_until(lambda: acquisition.snapshot().battery.ok)

    hal.sim_state.set_battery_fault(FaultMode.ABSENT)
    assert _wait_until(lambda: not acquisition.snapshot().battery.ok)
    # Les autres familles continuent pendant ce temps.
    assert acquisition.snapshot().temperatures[ZoneId.CABINE].ok

    hal.sim_state.set_battery_fault(FaultMode.OK)
    assert _wait_until(lambda: acquisition.snapshot().battery.ok, timeout_s=6.0)


def test_valve_command_travels_through_the_bus(rig) -> None:
    """L'interface ne touche jamais un pilote : elle dépose une commande."""
    _config, hal, bus, acquisition = rig
    hal.sim_state.set_valve_travel_time(CircuitId.LOCAL_EAU, 0.0)
    hal.sim_state.set_valve_feedback(CircuitId.LOCAL_EAU, True)

    assert _wait_until(
        lambda: _valve(acquisition, CircuitId.LOCAL_EAU) is not None
    )
    bus.submit(ManualValveCommand(circuit=CircuitId.LOCAL_EAU, action=ValveCommand.OPEN))

    assert _wait_until(
        lambda: _valve(acquisition, CircuitId.LOCAL_EAU) is not None
        and _valve(acquisition, CircuitId.LOCAL_EAU).confirmed is ConfirmedState.OUVERT
    )
    observation = _valve(acquisition, CircuitId.LOCAL_EAU)
    assert observation.commanded is ValveCommand.OPEN
    assert observation.state_is_certain is True


def test_valve_without_feedback_is_never_certain(rig) -> None:
    """Le cas matériel non tranché : commandé oui, confirmé jamais."""
    _config, hal, bus, acquisition = rig
    hal.sim_state.set_valve_feedback(CircuitId.CABINE, False)
    hal.sim_state.set_valve_travel_time(CircuitId.CABINE, 0.0)

    bus.submit(ManualValveCommand(circuit=CircuitId.CABINE, action=ValveCommand.OPEN))
    assert _wait_until(
        lambda: _valve(acquisition, CircuitId.CABINE) is not None
        and _valve(acquisition, CircuitId.CABINE).commanded is ValveCommand.OPEN
    )

    observation = _valve(acquisition, CircuitId.CABINE)
    assert observation.confirmed is ConfirmedState.INCONNU
    assert observation.state_is_certain is False
    assert observation.feedback_available is False


def test_faulty_actuator_is_reported_not_crashing(rig) -> None:
    _config, hal, bus, acquisition = rig
    hal.sim_state.set_valve_fault(CircuitId.LOCAL_BATTERIE, True)
    bus.submit(
        ManualValveCommand(circuit=CircuitId.LOCAL_BATTERIE, action=ValveCommand.OPEN)
    )

    assert _wait_until(
        lambda: _valve(acquisition, CircuitId.LOCAL_BATTERIE) is not None
        and _valve(acquisition, CircuitId.LOCAL_BATTERIE).fault
    )
    # Le reste de l'installation continue.
    assert acquisition.snapshot().temperatures[ZoneId.CABINE].ok


def test_stuck_sensor_is_replaced_and_the_rest_keeps_running(rig) -> None:
    """Le cas qu'aucun try/except ne règle : un pilote qui ne rend pas la main."""
    config, hal, _bus, acquisition = rig
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CABINE].ok)

    hal.sim_state.set_time_scale(1.0)       # une vraie attente, pas une accélérée
    hal.sim_state.set_temperature_fault(ZoneId.LOCAL_BATTERIE, FaultMode.STUCK)

    def temp_worker_stuck() -> bool:
        health = {item.name: item for item in acquisition.check_workers()}
        return health["temp_worker"].stuck or health["temp_worker"].restarts > 0

    assert _wait_until(temp_worker_stuck, timeout_s=8.0)

    # Pendant que le thread des températures est bloqué, tout le reste vit.
    snapshot = acquisition.snapshot()
    assert snapshot.battery.ok
    assert all(snapshot.levels[tank].ok for tank in TankId)

    hal.sim_state.set_temperature_fault(ZoneId.LOCAL_BATTERIE, FaultMode.OK)


def test_control_loop_publishes_without_touching_hardware(rig) -> None:
    config, _hal, _bus, acquisition = rig
    store = StateStore()
    received: list[object] = []
    store.add_listener(received.append)

    control = ControlWorker(
        acquisition, store,
        SnapshotBuilder(config, simulation=True), AlertEngine(config),
        period_s=0.05,
    )
    control.start()
    try:
        assert _wait_until(lambda: len(received) >= 3)
    finally:
        control.request_stop()
        control.join(timeout=1.0)

    published = store.get()
    assert published is not None
    assert published.simulation is True
    # L'instantané publié parle le vocabulaire de l'écran, pas celui des capteurs.
    assert published.temperatures[ZoneId.CABINE].label == "Cabine"
    assert set(published.circuits) == set(CircuitId)


def test_text_rendering_applies_the_display_rules(rig) -> None:
    """`--` pour une valeur absente, « Erreur capteur » pour une panne."""
    _config, hal, _bus, acquisition = rig
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CABINE].ok)

    hal.sim_state.set_temperature_fault(ZoneId.CELLULE, FaultMode.ERROR)
    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.CELLULE].status is Status.FAULT
    )

    text = format_snapshot(acquisition.snapshot())
    assert "Erreur capteur" in text
    assert "Local batterie" in text
    assert "Circuit 1" not in text       # jamais de nom générique
