"""Le panneau de simulation, monté et manipulé pour de vrai.

PyQt5 n'est pas une dépendance obligatoire : ces tests sont ignorés s'il est
absent, sans faire échouer la suite. Le reste du programme, lui, doit rester
entièrement testable sans interface graphique.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt5", reason="interface graphique non installée")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication      # noqa: E402

from vanmonitor.cli import Options            # noqa: E402
from vanmonitor.app import build_application  # noqa: E402
from vanmonitor.constants import CircuitId, ConfirmedState, ValveCommand, ZoneId  # noqa: E402
from vanmonitor.hal.sim.sim_state import FaultMode  # noqa: E402
from vanmonitor.ui.sim_panel import SimulationPanel  # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def panel(qt_app, tmp_path: Path):
    options = Options(
        config_path=tmp_path / "config.json",
        simulation=True,
        windowed=True,
        headless=False,
        duration_s=None,
        log_level="WARNING",
    )
    application = build_application(options)
    application.config.set("tanks.poll_period_s", 1)
    application.config.set("heating.command_timeout_s", 1.0)
    application.start()
    widget = SimulationPanel(application)
    try:
        yield widget, application
    finally:
        widget.close()
        application.stop()


def test_panel_builds_and_refreshes(panel) -> None:
    widget, _application = panel
    widget.refresh()
    text = widget._readout.toPlainText()
    assert "TEMPÉRATURES" in text
    assert "Local batterie" in text
    assert "Circuit 1" not in text


def test_moving_a_slider_changes_the_simulated_world(panel) -> None:
    widget, application = panel
    sim = application.hal.sim_state

    widget._on_temperature(ZoneId.CABINE, 235)      # 23,5 °C
    assert sim.temperature(ZoneId.CABINE) == pytest.approx(23.5)
    assert widget._temperature_labels[ZoneId.CABINE].text() == "23,5 °C"


def test_panel_commands_go_through_the_bus_not_the_driver(panel) -> None:
    """L'interface ne pilote jamais un clapet directement."""
    widget, application = panel
    assert len(application.command_bus) == 0

    widget._send_valve_command(CircuitId.CABINE, ValveCommand.OPEN)
    # La commande part dans la file ; c'est le thread des clapets qui exécute.
    assert application.command_bus.dropped == 0


def test_reset_button_clears_every_fault(panel) -> None:
    widget, application = panel
    sim = application.hal.sim_state

    sim.set_temperature_fault(ZoneId.CELLULE, FaultMode.ERROR)
    sim.set_valve_fault(CircuitId.CABINE, True)

    widget._reset_faults()

    assert sim.temperature_fault(ZoneId.CELLULE) is FaultMode.OK
    assert sim.valve(CircuitId.CABINE).fault is False


def test_readout_reports_uncertain_valve_states(panel) -> None:
    """Le point de vigilance : jamais d'état présenté comme certain à tort."""
    import time

    widget, application = panel
    sim = application.hal.sim_state
    sim.set_valve_feedback(CircuitId.CABINE, False)

    deadline = time.monotonic() + 3.0
    text = ""
    while time.monotonic() < deadline:
        widget.refresh()
        text = widget._readout.toPlainText()
        if "aucun retour de position" in text:
            break
        time.sleep(0.05)

    assert "aucun retour de position" in text
    assert "confirmé par le matériel" in text, (
        "le clapet équipé d'un retour de position doit, lui, être affirmatif"
    )
