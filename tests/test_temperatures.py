"""Gestion des températures : bus vivant, filtrage, association à chaud.

Quatre choses sont vérifiées ici, et ce sont les quatre qui font la différence
entre « une valeur s'affiche » et « la valeur affichée est la bonne » :

* le pilote DS18B20 lit les deux formats du noyau, contrôle la somme de
  redondance et refuse la valeur d'initialisation à 85 °C ;
* une trame isolée aberrante est écartée, mais un vrai changement brutal passe
  dès qu'une seconde mesure le confirme ;
* réassocier une sonde depuis les Paramètres prend effet **immédiatement**, et
  l'ancienne valeur n'est jamais attribuée à la nouvelle zone ;
* le bus est réellement balayé : une sonde débranchée disparaît de la liste.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vanmonitor.config import ConfigStore
from vanmonitor.constants import Status, ZoneId
from vanmonitor.core.acquisition import AcquisitionService
from vanmonitor.core.commands import CommandBus
from vanmonitor.core.filters import SpikeGuard
from vanmonitor.core.services import SnapshotBuilder, TemperatureService
from vanmonitor.hal.factory import build_hal
from vanmonitor.hal.interfaces import HardwareTimeout, SensorError
from vanmonitor.hal.real.ds18b20 import DS18B20Sensor, scan_sensor_ids
from vanmonitor.hal.sim.sim_state import SIM_SENSOR_IDS, FaultMode
from vanmonitor.models import Sample


def _wait_until(predicate, timeout_s: float = 6.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


# ---------------------------------------------------------------------------
# Pilote DS18B20
# ---------------------------------------------------------------------------

def _make_bus(root: Path, sensors: dict[str, str], *, style: str = "w1_slave") -> Path:
    """Reproduit l'arborescence exposée par le module noyau ``w1-therm``."""
    for sensor_id, content in sensors.items():
        directory = root / sensor_id
        directory.mkdir(parents=True, exist_ok=True)
        (directory / style).write_text(content, encoding="ascii")
    return root


def test_scan_finds_only_ds18b20_devices(tmp_path: Path) -> None:
    _make_bus(tmp_path, {
        "28-0316a2b4c5d6": "", "28-0316a2b4e7f8": "",
    })
    (tmp_path / "w1_bus_master1").mkdir()       # le maître du bus, pas une sonde
    assert scan_sensor_ids(tmp_path) == ["28-0316a2b4c5d6", "28-0316a2b4e7f8"]


def test_scan_without_a_bus_is_not_an_error(tmp_path: Path) -> None:
    assert scan_sensor_ids(tmp_path / "inexistant") == []


def test_reads_the_modern_temperature_file(tmp_path: Path) -> None:
    _make_bus(tmp_path, {"28-aaaa": "18375\n"}, style="temperature")
    sensor = DS18B20Sensor("28-aaaa", root=tmp_path)
    assert sensor.is_present()
    assert sensor.read_celsius() == pytest.approx(18.375)


def test_reads_the_classic_w1_slave_file(tmp_path: Path) -> None:
    _make_bus(tmp_path, {"28-bbbb":
        "3a 01 4b 46 7f ff 0c 10 crc=1c YES\n"
        "3a 01 4b 46 7f ff 0c 10 t=19625\n"})
    assert DS18B20Sensor("28-bbbb", root=tmp_path).read_celsius() == pytest.approx(19.625)


def test_a_bad_checksum_is_refused(tmp_path: Path) -> None:
    """Sur un bus long et secoué, une trame corrompue arrive vraiment."""
    _make_bus(tmp_path, {"28-cccc":
        "3a 01 4b 46 7f ff 0c 10 crc=1c NO\n"
        "3a 01 4b 46 7f ff 0c 10 t=19625\n"})
    with pytest.raises(SensorError, match="somme de contrôle"):
        DS18B20Sensor("28-cccc", root=tmp_path).read_celsius()


def test_the_power_on_value_is_not_a_temperature(tmp_path: Path) -> None:
    """85,000 °C est la valeur d'initialisation du registre, pas une mesure."""
    _make_bus(tmp_path, {"28-dddd": "85000\n"}, style="temperature")
    with pytest.raises(SensorError, match="conversion non aboutie"):
        DS18B20Sensor("28-dddd", root=tmp_path).read_celsius()


def test_a_value_outside_the_datasheet_range_is_refused(tmp_path: Path) -> None:
    _make_bus(tmp_path, {"28-eeee": "-99000\n"}, style="temperature")
    with pytest.raises(SensorError, match="hors des limites"):
        DS18B20Sensor("28-eeee", root=tmp_path).read_celsius()


def test_a_sensor_absent_from_the_bus_says_so(tmp_path: Path) -> None:
    sensor = DS18B20Sensor("28-ffff", root=tmp_path)
    assert sensor.is_present() is False
    with pytest.raises(SensorError, match="absente du bus"):
        sensor.read_celsius()


def test_a_deadline_already_spent_aborts_the_read(tmp_path: Path) -> None:
    _make_bus(tmp_path, {"28-1111": "18000\n"}, style="temperature")
    sensor = DS18B20Sensor("28-1111", timeout_s=0.1, root=tmp_path)
    time.sleep(0.15)
    # Le délai se compte depuis le début de la lecture : ici, la première
    # tentative part déjà en retard.
    with pytest.raises((HardwareTimeout, SensorError)):
        sensor._read_millidegrees(time.monotonic() - 1.0)


# ---------------------------------------------------------------------------
# Filtrage des valeurs aberrantes
# ---------------------------------------------------------------------------

def test_a_normal_evolution_is_never_delayed() -> None:
    guard = SpikeGuard(max_step=12.0)
    for value in (18.0, 18.3, 18.1, 17.6, 17.9):
        accepted, _reason = guard.accept(value)
        assert accepted is True
    assert guard.rejected == 0


def test_an_isolated_spike_is_dropped() -> None:
    guard = SpikeGuard(max_step=12.0)
    assert guard.accept(18.0)[0] is True

    accepted, reason = guard.accept(72.0)        # trame corrompue
    assert accepted is False
    assert "écart isolé" in reason

    assert guard.accept(18.2)[0] is True         # la mesure suivante dément
    assert guard.rejected == 1


def test_a_real_change_passes_as_soon_as_it_is_confirmed() -> None:
    """Une porte ouverte en hiver fait vraiment chuter la cabine."""
    guard = SpikeGuard(max_step=12.0)
    assert guard.accept(18.0)[0] is True
    assert guard.accept(2.0)[0] is False         # première mesure : douteuse
    assert guard.accept(1.6)[0] is True          # confirmée : elle passe
    assert guard.accept(1.4)[0] is True          # et la suite avec elle


def test_the_guard_forgets_everything_after_a_reset() -> None:
    guard = SpikeGuard(max_step=5.0)
    guard.accept(18.0)
    guard.reset()
    assert guard.accept(60.0)[0] is True         # nouvelle sonde, nouvel historique


# ---------------------------------------------------------------------------
# Service : décalage par zone
# ---------------------------------------------------------------------------

@pytest.fixture()
def config(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.json", debounce_s=0.0)
    store.load()
    return store


def test_the_zone_offset_is_applied(config: ConfigStore) -> None:
    config.set("temperatures.zones.cabine.offset_c", -1.5)
    readings = TemperatureService(config).build({
        ZoneId.CABINE: Sample(20.0, Status.OK, 100.0, 0.5),
    })
    assert readings[ZoneId.CABINE].celsius == pytest.approx(18.5)


def test_an_offset_is_not_applied_to_an_invalid_reading(config: ConfigStore) -> None:
    config.set("temperatures.zones.cabine.offset_c", -1.5)
    readings = TemperatureService(config).build({
        ZoneId.CABINE: Sample(20.0, Status.STALE, 100.0, 300.0),
    })
    assert readings[ZoneId.CABINE].celsius is None


# ---------------------------------------------------------------------------
# Bus vivant, association à chaud
# ---------------------------------------------------------------------------

@pytest.fixture()
def rig(tmp_path: Path):
    config = ConfigStore(tmp_path / "config.json", debounce_s=0.0)
    config.load()
    config.set("temperatures.poll_period_s", 1)
    config.set("temperatures.read_timeout_s", 0.5)

    hal = build_hal(config, simulation=True)
    hal.sim_state.set_time_scale(0.01)
    acquisition = AcquisitionService(hal, config, CommandBus())
    builder = SnapshotBuilder(config, simulation=True)
    acquisition.start()
    try:
        yield config, hal, acquisition, builder
    finally:
        acquisition.stop(timeout_s=1.0)


def test_the_bus_is_actually_scanned(rig) -> None:
    _config, _hal, acquisition, _builder = rig
    assert _wait_until(lambda: len(acquisition.snapshot().available_sensor_ids) == 5)
    assert set(acquisition.snapshot().available_sensor_ids) == set(SIM_SENSOR_IDS.values())


def test_an_unplugged_sensor_leaves_the_bus(rig) -> None:
    _config, hal, acquisition, _builder = rig
    assert _wait_until(lambda: len(acquisition.snapshot().available_sensor_ids) == 5)

    hal.sim_state.set_temperature_fault(ZoneId.COFFRE, FaultMode.ABSENT)
    assert _wait_until(
        lambda: SIM_SENSOR_IDS[ZoneId.COFFRE]
        not in acquisition.snapshot().available_sensor_ids
    )
    # Les quatre autres sont toujours là.
    assert len(acquisition.snapshot().available_sensor_ids) == 4

    hal.sim_state.set_temperature_fault(ZoneId.COFFRE, FaultMode.OK)
    assert _wait_until(lambda: len(acquisition.snapshot().available_sensor_ids) == 5)


def test_rebinding_a_zone_takes_effect_immediately(rig) -> None:
    """Le point qui manquait : une réassociation ne doit pas attendre le reboot."""
    config, _hal, acquisition, _builder = rig
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CABINE].ok)

    # La zone Cabine est priée de lire la sonde du Coffre.
    config.set("temperatures.zones.cabine.sensor_id", SIM_SENSOR_IDS[ZoneId.COFFRE])

    # La valeur de l'ancienne sonde est écartée sur-le-champ.
    assert acquisition.snapshot().temperatures[ZoneId.CABINE].status is not Status.OK

    # Puis la zone lit bien la nouvelle sonde : 9,8 °C du coffre, pas 18,2.
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CABINE].ok)
    value = acquisition.snapshot().temperatures[ZoneId.CABINE].value
    assert value == pytest.approx(9.8, abs=0.3)


def test_unbinding_a_zone_shows_dashes(rig) -> None:
    config, _hal, acquisition, builder = rig
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CELLULE].ok)

    # En simulation, la zone est associée d'office ; on l'associe d'abord
    # explicitement, puis on la libère.
    config.set("temperatures.zones.cellule.sensor_id", SIM_SENSOR_IDS[ZoneId.CELLULE])
    assert _wait_until(lambda: acquisition.snapshot().temperatures[ZoneId.CELLULE].ok)

    config.set("temperatures.zones.cellule.sensor_id", None)
    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.CELLULE].status
        is Status.ABSENT
    )
    reading = builder.build(acquisition.snapshot()).temperatures[ZoneId.CELLULE]
    assert reading.celsius is None


def test_identification_reads_unbound_sensors_only_on_demand(rig) -> None:
    """Lire une sonde libre coûte une seconde de bus : c'est à la demande."""
    config, _hal, acquisition, _builder = rig
    config.set("temperatures.zones.cabine.sensor_id", None)
    config.set("temperatures.zones.cellule.sensor_id", SIM_SENSOR_IDS[ZoneId.CABINE])

    assert _wait_until(lambda: acquisition.snapshot().available_sensor_ids)
    # Hors identification, aucune sonde libre n'est lue.
    time.sleep(1.2)
    free_id = SIM_SENSOR_IDS[ZoneId.CELLULE]
    assert free_id not in acquisition.snapshot().sensor_temperatures

    acquisition.set_identification_mode(True)
    assert _wait_until(
        lambda: free_id in acquisition.snapshot().sensor_temperatures
    ), "la sonde libre devrait être lue pendant l'identification"
    assert acquisition.snapshot().sensor_temperatures[free_id].ok

    acquisition.set_identification_mode(False)
    assert acquisition.snapshot().sensor_temperatures == {}


def test_one_faulty_sensor_does_not_stop_the_scan(rig) -> None:
    _config, hal, acquisition, _builder = rig
    hal.sim_state.set_temperature_fault(ZoneId.LOCAL_EAU, FaultMode.ERROR)

    assert _wait_until(
        lambda: acquisition.snapshot().temperatures[ZoneId.LOCAL_EAU].status
        is Status.FAULT
    )
    snapshot = acquisition.snapshot()
    assert len(snapshot.available_sensor_ids) == 5      # elle est toujours sur le bus
    for zone in (ZoneId.CABINE, ZoneId.COFFRE, ZoneId.CELLULE, ZoneId.LOCAL_BATTERIE):
        assert snapshot.temperatures[zone].ok
