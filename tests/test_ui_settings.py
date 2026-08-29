"""La page Paramètres, et surtout le seul réglage qui demande confirmation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt5", reason="interface graphique non installée")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QDialog, QLabel      # noqa: E402

from vanmonitor.app import build_application                   # noqa: E402
from vanmonitor.cli import Options                             # noqa: E402
from vanmonitor.constants import CircuitId, HeatingMode, SensorLossFallback  # noqa: E402
from vanmonitor.core.calibration import CalibrationPoint, CalibrationTable   # noqa: E402
from vanmonitor.ui.settings_page import SECTIONS               # noqa: E402


@pytest.fixture(scope="module")
def qt_app():
    yield QApplication.instance() or QApplication([])


@pytest.fixture()
def window(qt_app, tmp_path: Path):
    options = Options(
        config_path=tmp_path / "config.json", simulation=True, windowed=True,
        headless=False, duration_s=None, log_level="WARNING",
        screen_size=(800, 480), no_sim_panel=True,
    )
    application = build_application(options)
    application.start()
    from vanmonitor.ui.main_window import MainWindow
    widget = MainWindow(application)
    widget.show_settings("heating")
    try:
        yield widget, application
    finally:
        widget.close()
        application.stop()


def _texts(widget) -> list[str]:
    return [child.text() for child in widget.findChildren(QLabel)]


def test_the_settings_sections_are_present(window) -> None:
    widget, _application = window
    assert [key for key, _text in SECTIONS] == [
        "heating", "alerts", "calibration", "sensors", "display", "history",
    ]
    for key, _text in SECTIONS:
        widget.show_settings(key)
        assert widget._settings._stack.currentWidget() is widget._settings._scrolls[key]


# ---------------------------------------------------------------------------
# Repli sur perte de sonde : réglage de sécurité
# ---------------------------------------------------------------------------

def _circuit_block(widget, circuit: CircuitId):
    return widget._settings._sections["heating"]._blocks[circuit]


def test_cancelling_the_confirmation_leaves_the_fallback_untouched(window, monkeypatch) -> None:
    widget, application = window
    block = _circuit_block(widget, CircuitId.LOCAL_EAU)
    heating = application.builder.heating
    assert heating.fallback(CircuitId.LOCAL_EAU) is SensorLossFallback.OPEN

    monkeypatch.setattr(QDialog, "exec_", lambda self: 0)       # l'utilisateur annule
    accepted = block._set_fallback("close")

    assert accepted is False
    assert heating.fallback(CircuitId.LOCAL_EAU) is SensorLossFallback.OPEN
    assert application.config.get(
        "heating.circuits.local_eau.on_sensor_loss") == "open"


def test_confirming_applies_the_fallback(window, monkeypatch) -> None:
    widget, application = window
    block = _circuit_block(widget, CircuitId.CABINE)
    heating = application.builder.heating
    assert heating.fallback(CircuitId.CABINE) is SensorLossFallback.HOLD

    monkeypatch.setattr(QDialog, "exec_", lambda self: 1)       # l'utilisateur confirme
    accepted = block._set_fallback("open")

    assert accepted is True
    assert heating.fallback(CircuitId.CABINE) is SensorLossFallback.OPEN


def test_defaults_match_the_validated_architecture(window) -> None:
    _widget, application = window
    heating = application.builder.heating
    assert heating.fallback(CircuitId.LOCAL_EAU) is SensorLossFallback.OPEN
    assert heating.fallback(CircuitId.LOCAL_BATTERIE) is SensorLossFallback.OPEN
    assert heating.fallback(CircuitId.CABINE) is SensorLossFallback.HOLD


# ---------------------------------------------------------------------------
# Modes et seuils
# ---------------------------------------------------------------------------

def test_auto_is_refused_while_thresholds_are_undefined(window) -> None:
    _widget, application = window
    heating = application.builder.heating
    assert heating.thresholds_defined(CircuitId.CABINE) is False
    assert heating.set_mode(CircuitId.CABINE, HeatingMode.AUTO) is False
    assert heating.mode(CircuitId.CABINE) is HeatingMode.MANUEL


def test_auto_becomes_available_once_thresholds_are_set(window) -> None:
    _widget, application = window
    heating = application.builder.heating
    heating.set_thresholds(CircuitId.CABINE, 12.0, 16.0)
    assert heating.set_mode(CircuitId.CABINE, HeatingMode.AUTO) is True
    assert heating.mode(CircuitId.CABINE) is HeatingMode.AUTO


def test_incoherent_thresholds_are_refused(window) -> None:
    _widget, application = window
    heating = application.builder.heating
    with pytest.raises(ValueError):
        heating.set_thresholds(CircuitId.LOCAL_EAU, 10.0, 10.5)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_calibration_refuses_an_incoherent_table(window) -> None:
    from vanmonitor.constants import TankId
    from vanmonitor.core.calibration import CalibrationError

    _widget, application = window
    tanks = application.builder.tanks
    table = CalibrationTable(
        [CalibrationPoint(0.0, 0.0), CalibrationPoint(0.5, 40.0),
         CalibrationPoint(0.8, 20.0)],       # redescend : incohérent
        unit="litres", capacity_l=None,
    )
    with pytest.raises(CalibrationError):
        tanks.save_table(TankId.EAU_PROPRE, table)


def test_calibration_saves_a_valid_table(window) -> None:
    from vanmonitor.constants import TankId

    _widget, application = window
    tanks = application.builder.tanks
    table = CalibrationTable(
        [CalibrationPoint(0.0, 0.0), CalibrationPoint(1.0, 90.0)],
        unit="litres", capacity_l=None,
    )
    tanks.save_table(TankId.EAU_PROPRE, table)

    saved = tanks.table(TankId.EAU_PROPRE)
    assert saved.is_valid()
    assert saved.effective_capacity() == pytest.approx(90.0)
    litres, out_of_range = saved.litres(0.5)
    assert litres == pytest.approx(45.0)
    assert out_of_range is False
