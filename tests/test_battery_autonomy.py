"""Autonomie restante : aucune valeur ancienne ne doit être présentée comme actuelle.

C'est la vérification demandée après l'étape 3. Le risque est précis : le
dernier ``BatteryReading`` reçu reste en mémoire dans le ``LatestValue`` — c'est
voulu, il sert au diagnostic — et rien n'empêcherait, par inadvertance, de
continuer à l'afficher après une coupure de la liaison VE.Direct.

Les tests ci-dessous verrouillent quatre choses :

* une lecture ``STALE`` ou ``FAULT`` ne produit **aucune** grandeur affichable,
  autonomie comprise ;
* une autonomie invraisemblable est écartée ;
* l'absence d'autonomie fait disparaître la ligne, sans « N/A » ;
* de bout en bout, couper la liaison efface l'autonomie de l'écran.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from vanmonitor.config import ConfigStore
from vanmonitor.constants import Status
from vanmonitor.core.acquisition import AcquisitionService
from vanmonitor.core.commands import CommandBus
from vanmonitor.core.services import BatteryService, SnapshotBuilder
from vanmonitor.core.state import LatestValue
from vanmonitor.hal.factory import build_hal
from vanmonitor.hal.sim.sim_state import FaultMode, SimBattery
from vanmonitor.models import BatteryReading, Sample


@pytest.fixture()
def config(tmp_path: Path) -> ConfigStore:
    store = ConfigStore(tmp_path / "config.json", debounce_s=0.0)
    store.load()
    return store


def _fresh_reading(**overrides) -> BatteryReading:
    fields = dict(
        soc_percent=87.0, voltage_v=13.2, current_a=-4.2, power_w=-55.0,
        consumed_ah=-12.0, time_to_go_min=1080, status=Status.OK, updated_at=100.0,
    )
    fields.update(overrides)
    return BatteryReading(**fields)


# ---------------------------------------------------------------------------
# Le service ne recycle jamais une lecture périmée
# ---------------------------------------------------------------------------

def test_fresh_reading_keeps_its_autonomy(config: ConfigStore) -> None:
    reading = BatteryService(config).build(
        Sample(_fresh_reading(), Status.OK, 100.0, 0.5)
    )
    assert reading.status is Status.OK
    assert reading.time_to_go_min == 1080


def test_stale_reading_drops_everything_including_autonomy(config: ConfigStore) -> None:
    """Une donnée trop vieille n'est pas une donnée : elle ne s'affiche pas."""
    reading = BatteryService(config).build(
        Sample(_fresh_reading(), Status.STALE, 100.0, 300.0,
               reason="aucune lecture depuis 300 s")
    )
    assert reading.status is Status.STALE
    assert reading.time_to_go_min is None
    assert reading.soc_percent is None
    assert reading.voltage_v is None


def test_faulty_reading_drops_everything_including_autonomy(config: ConfigStore) -> None:
    reading = BatteryService(config).build(
        Sample(_fresh_reading(), Status.FAULT, 100.0, 1.0, reason="liaison coupée")
    )
    assert reading.status is Status.FAULT
    assert reading.time_to_go_min is None
    assert reading.soc_percent is None


def test_absent_link_produces_nothing(config: ConfigStore) -> None:
    reading = BatteryService(config).build(
        Sample(None, Status.ABSENT, None, None, reason="liaison non intégrée")
    )
    assert reading.time_to_go_min is None
    assert reading.soc_percent is None


# ---------------------------------------------------------------------------
# Plausibilité
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [0, -10, 6001, 999999])
def test_implausible_autonomy_is_discarded(config: ConfigStore, value: int) -> None:
    """Une autonomie nulle, négative ou démesurée vaut moins que rien."""
    reading = BatteryService(config).build(
        Sample(_fresh_reading(time_to_go_min=value), Status.OK, 100.0, 0.5)
    )
    assert reading.time_to_go_min is None
    assert reading.soc_percent == 87.0      # le reste de la lecture est intact


def test_autonomy_can_be_switched_off_entirely(config: ConfigStore) -> None:
    config.set("battery.show_time_to_go", False)
    reading = BatteryService(config).build(
        Sample(_fresh_reading(), Status.OK, 100.0, 0.5)
    )
    assert reading.time_to_go_min is None


def test_shunt_that_never_reports_autonomy(config: ConfigStore) -> None:
    reading = BatteryService(config).build(
        Sample(_fresh_reading(time_to_go_min=None), Status.OK, 100.0, 0.5)
    )
    assert reading.time_to_go_min is None
    assert reading.status is Status.OK       # ce n'est pas une panne


# ---------------------------------------------------------------------------
# Le slot conserve la valeur, le service refuse de la resservir
# ---------------------------------------------------------------------------

def test_the_slot_keeps_the_value_but_marks_it_unusable(config: ConfigStore) -> None:
    """Le diagnostic garde la dernière lecture ; l'affichage ne s'en sert pas."""
    slot = LatestValue("battery")
    slot.set(_fresh_reading(), measured_at=100.0)
    slot.mark_fault("liaison VE.Direct coupée")

    sample = slot.get(now=101.0)
    assert sample.value is not None          # la valeur est toujours là…
    assert sample.status is Status.FAULT     # …mais elle n'est plus valide

    reading = BatteryService(config).build(sample)
    assert reading.time_to_go_min is None    # et elle n'atteint jamais l'écran
    assert reading.soc_percent is None


def test_staleness_is_computed_from_the_last_successful_read() -> None:
    slot = LatestValue("battery")
    slot.set(_fresh_reading(), measured_at=100.0)

    assert slot.get(now=105.0, stale_after_s=15).status is Status.OK
    assert slot.get(now=130.0, stale_after_s=15).status is Status.STALE


# ---------------------------------------------------------------------------
# Le SmartShunt simulé annonce une autonomie cohérente
# ---------------------------------------------------------------------------

def test_simulated_shunt_recomputes_autonomy_from_the_state_of_charge() -> None:
    """Baisser l'état de charge doit faire baisser l'autonomie, pas la figer."""
    full = SimBattery(soc_percent=87.0, current_a=-4.2, consumed_ah=-12.0)
    low = SimBattery(soc_percent=17.0, current_a=-8.4, consumed_ah=-96.0)

    assert full.computed_time_to_go_min() > low.computed_time_to_go_min()
    assert low.computed_time_to_go_min() < 300      # moins de cinq heures


def test_simulated_shunt_reports_no_autonomy_while_charging() -> None:
    charging = SimBattery(soc_percent=60.0, current_a=+12.0, consumed_ah=-40.0)
    assert charging.computed_time_to_go_min() is None


# ---------------------------------------------------------------------------
# De bout en bout
# ---------------------------------------------------------------------------

def _wait_until(predicate, timeout_s: float = 6.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


def test_cutting_the_link_removes_the_autonomy_from_the_snapshot(tmp_path: Path) -> None:
    """Le cas redouté, joué en entier : la liaison tombe, l'autonomie disparaît."""
    config = ConfigStore(tmp_path / "config.json", debounce_s=0.0)
    config.load()
    config.set("battery.poll_period_s", 0.2)
    config.set("battery.stale_after_s", 1)
    config.set("battery.reconnect_backoff_s", [0.05])

    hal = build_hal(config, simulation=True)
    hal.sim_state.set_time_scale(0.01)
    acquisition = AcquisitionService(hal, config, CommandBus())
    builder = SnapshotBuilder(config, simulation=True)
    acquisition.start()
    try:
        assert _wait_until(
            lambda: builder.build(acquisition.snapshot()).battery.time_to_go_min
            is not None
        ), "l'autonomie devrait être publiée tant que la liaison répond"

        hal.sim_state.set_battery_fault(FaultMode.ABSENT)

        assert _wait_until(
            lambda: builder.build(acquisition.snapshot()).battery.status is not Status.OK
        )
        battery = builder.build(acquisition.snapshot()).battery
        assert battery.time_to_go_min is None, (
            "une autonomie mémorisée ne doit jamais survivre à la coupure"
        )
        assert battery.soc_percent is None
    finally:
        acquisition.stop(timeout_s=1.0)
