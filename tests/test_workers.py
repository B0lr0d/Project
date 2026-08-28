"""Les garanties du modèle d'acquisition, vérifiées.

Trois choses sont éprouvées ici, et ce sont exactement les trois demandées :

1. une lecture en erreur n'arrête pas le thread ;
2. une lecture **lente** n'empêche pas les autres familles d'avancer ;
3. une lecture **bloquée** est détectée par le chien de garde, le thread est
   abandonné et remplacé — sans que le reste du programme s'arrête.
"""

from __future__ import annotations

import threading
import time

import pytest

from vanmonitor.constants import Status
from vanmonitor.core.state import LatestValue
from vanmonitor.core.workers import HardwareWorker, WorkerSupervisor
from vanmonitor.hal.interfaces import SensorError


def _wait_until(predicate, timeout_s: float = 3.0, step_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


# ---------------------------------------------------------------------------
# LatestValue
# ---------------------------------------------------------------------------

def test_latest_value_starts_absent() -> None:
    slot = LatestValue("test")
    sample = slot.get()
    assert sample.status is Status.ABSENT
    assert sample.value is None
    assert not sample.ok


def test_latest_value_becomes_stale_when_too_old() -> None:
    slot = LatestValue("test")
    slot.set(21.0, measured_at=100.0)

    fresh = slot.get(now=100.5, stale_after_s=10.0)
    assert fresh.status is Status.OK

    old = slot.get(now=200.0, stale_after_s=10.0)
    assert old.status is Status.STALE
    assert old.age_s == pytest.approx(100.0)


def test_fault_keeps_the_last_known_value_but_not_its_validity() -> None:
    slot = LatestValue("test")
    slot.set(21.0)
    slot.mark_fault("capteur muet")

    sample = slot.get()
    assert sample.status is Status.FAULT
    assert sample.reason == "capteur muet"
    assert not sample.ok


# ---------------------------------------------------------------------------
# HardwareWorker
# ---------------------------------------------------------------------------

def test_worker_publishes_and_survives_errors() -> None:
    slot = LatestValue("test")
    calls = {"count": 0}

    def task() -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise SensorError("panne passagère")
        slot.set(float(calls["count"]))

    worker = HardwareWorker("test_worker", task, period_s=0.02, deadline_s=1.0)
    worker.start()
    try:
        assert _wait_until(lambda: calls["count"] >= 4)
    finally:
        worker.request_stop()
        worker.join(timeout=1.0)

    # La panne du deuxième tour n'a pas arrêté la boucle.
    assert calls["count"] >= 4
    assert slot.get().ok
    assert worker.health().consecutive_failures == 0


def test_slow_family_does_not_delay_the_others() -> None:
    """La garantie centrale : chaque famille avance à son rythme."""
    slow_calls = {"count": 0}
    fast_calls = {"count": 0}

    def slow_task() -> None:
        slow_calls["count"] += 1
        time.sleep(0.5)

    def fast_task() -> None:
        fast_calls["count"] += 1

    slow = HardwareWorker("slow", slow_task, period_s=0.0, deadline_s=10.0)
    fast = HardwareWorker("fast", fast_task, period_s=0.01, deadline_s=10.0)
    slow.start()
    fast.start()
    try:
        assert _wait_until(lambda: fast_calls["count"] >= 20, timeout_s=2.0)
    finally:
        slow.request_stop()
        fast.request_stop()
        fast.join(timeout=1.0)
        slow.join(timeout=2.0)

    # Le thread lent a à peine tourné pendant que le rapide enchaînait.
    assert fast_calls["count"] >= 20
    assert slow_calls["count"] <= 5


# ---------------------------------------------------------------------------
# Chien de garde
# ---------------------------------------------------------------------------

def test_supervisor_detects_and_replaces_a_stuck_worker() -> None:
    """Un thread bloqué est déclaré tel, abandonné, et remplacé."""
    release = threading.Event()
    started = []

    def stuck_task() -> None:
        started.append(time.monotonic())
        release.wait(timeout=10.0)      # bloqué tant qu'on ne le libère pas

    supervisor = WorkerSupervisor(backoff_s=[0.0])
    supervisor.register(lambda: HardwareWorker(
        "stuck_worker", stuck_task, period_s=0.01, deadline_s=0.1,
    ))
    supervisor.start_all()
    try:
        assert _wait_until(lambda: len(started) >= 1)

        # Au-delà de l'échéance, le superviseur doit le voir bloqué…
        time.sleep(0.2)
        first_report = supervisor.health()
        assert first_report[0].stuck is True

        # …et le remplacer au tour de contrôle suivant.
        supervisor.check()
        assert _wait_until(lambda: len(started) >= 2)
        assert supervisor.health()[0].restarts >= 1
    finally:
        release.set()
        supervisor.stop_all(timeout_s=1.0)


def test_supervisor_backoff_prevents_restart_storms() -> None:
    """Un thread durablement bloqué ne doit pas engendrer des threads en rafale."""
    release = threading.Event()
    started = []

    def stuck_task() -> None:
        started.append(time.monotonic())
        release.wait(timeout=10.0)

    supervisor = WorkerSupervisor(backoff_s=[60.0])
    supervisor.register(lambda: HardwareWorker(
        "stuck_worker", stuck_task, period_s=0.01, deadline_s=0.05,
    ))
    supervisor.start_all()
    try:
        assert _wait_until(lambda: len(started) >= 1)
        time.sleep(0.15)
        for _ in range(20):             # vingt tours de boucle de contrôle
            supervisor.check()
            time.sleep(0.01)
        # Un seul remplaçant malgré vingt vérifications.
        assert len(started) == 2
    finally:
        release.set()
        supervisor.stop_all(timeout_s=1.0)


def test_a_late_measurement_never_overwrites_a_fresher_one() -> None:
    """Un thread abandonné qui se débloque tard ne doit rien écraser.

    C'est la contrepartie de la limite assumée : on ne peut pas tuer un thread
    bloqué, donc il faut que sa publication tardive soit sans effet. Chaque
    mesure est datée de son **début**, et une mesure antérieure à celle déjà
    publiée est refusée.
    """
    slot = LatestValue("test")
    release = threading.Event()

    def task() -> None:
        started = time.monotonic()      # la mesure commence ici…
        release.wait(timeout=5.0)       # …et se débloque bien plus tard
        slot.set("valeur périmée", measured_at=started)

    worker = HardwareWorker("late", task, period_s=0.01, deadline_s=0.05)
    worker.start()
    time.sleep(0.1)

    worker.retire()                     # le superviseur le met hors service
    slot.set("valeur récente")          # le remplaçant publie
    release.set()
    worker.join(timeout=2.0)

    assert slot.get().value == "valeur récente"


def test_out_of_order_fault_is_ignored() -> None:
    """Un thread abandonné ne doit pas faire passer en panne un capteur sain."""
    slot = LatestValue("test")
    stale_moment = time.monotonic()
    slot.set("valeur récente")

    assert slot.mark_fault("panne constatée avant", measured_at=stale_moment) is False
    assert slot.get().ok
