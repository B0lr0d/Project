"""Le matériel simulé doit se comporter comme du matériel, pannes comprises.

C'est la garantie qui évite que le simulateur mente (risque R-17) : s'il ne
sait que fonctionner, il ne prouve rien.
"""

from __future__ import annotations

import pytest

from vanmonitor.constants import (
    CircuitId,
    ConfirmedState,
    TankId,
    ValveCommand,
    ValveState,
    ZoneId,
)
from vanmonitor.hal.interfaces import (
    HardwareTimeout,
    LevelSensor,
    LinkError,
    SensorError,
    SmartShuntInterface,
    TemperatureSensor,
    ValveDriver,
    ValveError,
)
from vanmonitor.hal.sim import (
    FaultMode,
    MockLevelSensor,
    MockSmartShuntInterface,
    MockTemperatureSensor,
    MockValveDriver,
    SimState,
)


@pytest.fixture()
def sim() -> SimState:
    state = SimState()
    state.set_time_scale(0.01)      # les pannes lentes ne doivent pas ralentir les tests
    return state


# ---------------------------------------------------------------------------
# Températures
# ---------------------------------------------------------------------------

def test_temperature_sensor_implements_the_interface(sim: SimState) -> None:
    sensor = MockTemperatureSensor(ZoneId.CABINE, sim)
    assert isinstance(sensor, TemperatureSensor)
    assert sensor.sensor_id().startswith("28-")
    assert sensor.is_present()


def test_temperature_follows_the_simulated_world(sim: SimState) -> None:
    sensor = MockTemperatureSensor(ZoneId.CABINE, sim)
    sim.set_temperature(ZoneId.CABINE, 21.0)
    assert sensor.read_celsius() == pytest.approx(21.0, abs=0.1)


def test_absent_sensor_is_not_present_and_raises(sim: SimState) -> None:
    sensor = MockTemperatureSensor(ZoneId.CELLULE, sim)
    sim.set_temperature_fault(ZoneId.CELLULE, FaultMode.ABSENT)

    assert not sensor.is_present()
    with pytest.raises(SensorError):
        sensor.read_celsius()


def test_read_error_raises_sensor_error(sim: SimState) -> None:
    sensor = MockTemperatureSensor(ZoneId.COFFRE, sim)
    sim.set_temperature_fault(ZoneId.COFFRE, FaultMode.ERROR)
    with pytest.raises(SensorError):
        sensor.read_celsius()


def test_slow_sensor_honours_its_deadline(sim: SimState) -> None:
    """Une lecture lente doit abandonner elle-même, pas s'éterniser."""
    sensor = MockTemperatureSensor(ZoneId.LOCAL_EAU, sim, timeout_s=1.0)
    sim.set_temperature_fault(ZoneId.LOCAL_EAU, FaultMode.SLOW)
    with pytest.raises(HardwareTimeout):
        sensor.read_celsius()


def test_faults_are_independent_between_zones(sim: SimState) -> None:
    """Une sonde en panne n'empêche pas les autres de répondre."""
    broken = MockTemperatureSensor(ZoneId.LOCAL_EAU, sim)
    healthy = MockTemperatureSensor(ZoneId.CABINE, sim)
    sim.set_temperature_fault(ZoneId.LOCAL_EAU, FaultMode.ERROR)

    with pytest.raises(SensorError):
        broken.read_celsius()
    assert healthy.read_celsius() is not None


# ---------------------------------------------------------------------------
# Niveaux
# ---------------------------------------------------------------------------

def test_level_sensor_returns_raw_values(sim: SimState) -> None:
    sensor = MockLevelSensor(TankId.EAU_PROPRE, sim)
    assert isinstance(sensor, LevelSensor)
    sim.set_level(TankId.EAU_PROPRE, 0.5)
    assert sensor.read_raw() == pytest.approx(0.5, abs=0.01)


def test_level_sensor_failure(sim: SimState) -> None:
    sensor = MockLevelSensor(TankId.GASOIL, sim)
    sim.set_level_fault(TankId.GASOIL, FaultMode.ERROR)
    with pytest.raises(SensorError):
        sensor.read_raw()


# ---------------------------------------------------------------------------
# SmartShunt
# ---------------------------------------------------------------------------

def test_smartshunt_requires_connection_first(sim: SimState) -> None:
    shunt = MockSmartShuntInterface(sim)
    assert isinstance(shunt, SmartShuntInterface)
    with pytest.raises(LinkError):
        shunt.read()

    shunt.connect()
    reading = shunt.read()
    assert reading.soc_percent == pytest.approx(87.0)
    assert reading.power_w == pytest.approx(reading.voltage_v * reading.current_a)


def test_smartshunt_link_loss_closes_the_link(sim: SimState) -> None:
    """Une liaison série tombée doit être rouverte explicitement."""
    shunt = MockSmartShuntInterface(sim)
    shunt.connect()
    sim.set_battery_fault(FaultMode.ERROR)

    with pytest.raises(LinkError):
        shunt.read()
    assert not shunt.is_connected()

    sim.set_battery_fault(FaultMode.OK)
    shunt.connect()
    assert shunt.read().soc_percent is not None


def test_smartshunt_can_withhold_time_to_go(sim: SimState) -> None:
    """L'autonomie n'est pas toujours fournie : le programme doit l'accepter."""
    shunt = MockSmartShuntInterface(sim)
    shunt.connect()
    sim.update_battery(time_to_go_available=False)
    assert shunt.read().time_to_go_min is None


# ---------------------------------------------------------------------------
# Clapets — le point le plus sensible
# ---------------------------------------------------------------------------

def test_valve_without_feedback_never_confirms_anything(sim: SimState) -> None:
    """Sans retour de position, ``confirmed`` reste INCONNU, quoi qu'il arrive."""
    sim.set_valve_feedback(CircuitId.CABINE, False)
    sim.set_valve_travel_time(CircuitId.CABINE, 0.0)
    driver = MockValveDriver(CircuitId.CABINE, sim)
    assert isinstance(driver, ValveDriver)

    assert not driver.has_position_feedback()
    assert driver.get_commanded_state() is ValveCommand.NONE
    assert driver.get_confirmed_state() is ConfirmedState.INCONNU
    assert driver.get_state() is ValveState.INCONNU

    driver.open()
    assert driver.get_commanded_state() is ValveCommand.OPEN
    # L'ordre est passé, la course est finie… et pourtant rien n'est confirmé.
    assert driver.get_state() is ValveState.OUVERT
    assert driver.get_confirmed_state() is ConfirmedState.INCONNU


def test_valve_with_feedback_confirms_after_travel(sim: SimState) -> None:
    sim.set_valve_feedback(CircuitId.LOCAL_EAU, True)
    sim.set_valve_travel_time(CircuitId.LOCAL_EAU, 0.0)
    driver = MockValveDriver(CircuitId.LOCAL_EAU, sim)

    driver.open()
    assert driver.get_confirmed_state() is ConfirmedState.OUVERT
    assert driver.get_state() is ValveState.OUVERT

    driver.close()
    assert driver.get_confirmed_state() is ConfirmedState.FERME
    assert driver.get_state() is ValveState.FERME


def test_valve_reports_transition_while_moving(sim: SimState) -> None:
    sim.set_valve_feedback(CircuitId.LOCAL_BATTERIE, True)
    sim.set_valve_travel_time(CircuitId.LOCAL_BATTERIE, 30.0)
    driver = MockValveDriver(CircuitId.LOCAL_BATTERIE, sim)

    driver.open()
    assert driver.get_state() is ValveState.OUVERTURE
    # En pleine course, la position n'est ni ouverte ni fermée.
    assert driver.get_confirmed_state() is ConfirmedState.INCONNU


def test_faulty_actuator_refuses_orders(sim: SimState) -> None:
    sim.set_valve_fault(CircuitId.CABINE, True)
    driver = MockValveDriver(CircuitId.CABINE, sim)

    assert driver.has_fault()
    assert driver.get_state() is ValveState.ERREUR
    with pytest.raises(ValveError):
        driver.open()


def test_stop_freezes_the_valve_mid_travel(sim: SimState) -> None:
    sim.set_valve_feedback(CircuitId.LOCAL_EAU, True)
    sim.set_valve_travel_time(CircuitId.LOCAL_EAU, 30.0)
    driver = MockValveDriver(CircuitId.LOCAL_EAU, sim)

    driver.open()
    driver.stop()
    assert driver.get_commanded_state() is ValveCommand.STOP
    # Arrêté entre deux positions : rien à confirmer.
    assert driver.get_confirmed_state() is ConfirmedState.INCONNU


def test_default_world_exposes_both_valve_variants(sim: SimState) -> None:
    """Les deux cas matériels doivent être visibles dès le premier lancement."""
    feedback = {
        circuit: MockValveDriver(circuit, sim).has_position_feedback()
        for circuit in CircuitId
    }
    assert any(feedback.values()), "aucun clapet avec retour de position"
    assert not all(feedback.values()), "aucun clapet sans retour de position"
