"""L'écran d'accueil, monté et éprouvé sur des instantanés réels.

Les tests portent sur ce que l'écran **dit**, pas sur son apparence : un état
de clapet non confirmé doit être annoncé comme tel, une sonde absente doit
afficher ``--`` et non « Erreur capteur », et aucun circuit ne doit jamais
s'appeler « Circuit 1 ».
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("PyQt5", reason="interface graphique non installée")

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QLabel        # noqa: E402

from vanmonitor.app import build_application            # noqa: E402
from vanmonitor.cli import Options                      # noqa: E402
from vanmonitor.constants import (                      # noqa: E402
    CIRCUIT_ORDER,
    CircuitId,
    ConfirmedState,
    HeatingMode,
    SensorLossFallback,
    Status,
    TankId,
    ValveCommand,
    ValveState,
    ZoneId,
)
from vanmonitor.models import (                         # noqa: E402
    Alert,
    BatteryReading,
    CircuitStatus,
    SystemSnapshot,
    TankReading,
    TemperatureReading,
)
from vanmonitor.constants import AlertLevel             # noqa: E402
from vanmonitor.ui.layout_profile import LayoutProfile, profile_for   # noqa: E402
from vanmonitor.ui.theme import metrics_for             # noqa: E402
from vanmonitor.ui.widgets.cards import _valve_appearance            # noqa: E402


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
    try:
        yield widget, application
    finally:
        widget.close()
        application.stop()


def _texts(widget) -> list[str]:
    return [child.text() for child in widget.findChildren(QLabel)]


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def test_home_shows_every_named_block(window) -> None:
    widget, _application = window
    widget.refresh()
    texts = " | ".join(_texts(widget))

    for expected in ("ÉNERGIE — BATTERIE (SMARTSHUNT)", "EAU PROPRE", "EAUX GRISES",
                     "GASOIL", "TEMPÉRATURES", "CHAUFFAGE — ÉLECTROVANNES"):
        assert expected in texts, f"bloc manquant : {expected}"

    for zone in ("Local batterie", "Local eau", "Coffre", "Cabine", "Cellule"):
        assert zone in texts, f"zone manquante : {zone}"


def test_circuits_are_never_numbered(window) -> None:
    widget, _application = window
    widget.refresh()
    texts = " | ".join(_texts(widget))
    for forbidden in ("Circuit 1", "Circuit 2", "Circuit 3"):
        assert forbidden not in texts
    for named in ("Local eau", "Local batterie", "Cabine"):
        assert named in texts


def test_no_alert_says_so(window) -> None:
    widget, application = window
    application.control.tick()
    widget.refresh()
    assert "Aucune alerte" in _texts(widget)


def test_active_alerts_are_listed(window) -> None:
    widget, _application = window
    alert = Alert(key="batterie_basse", level=AlertLevel.CRITIQUE,
                  message="Batterie 17 %", active_since=0.0)
    widget._alerts.update_alerts((alert,))
    texts = _texts(widget)
    assert "Batterie 17 %" in texts
    assert "Aucune alerte" not in texts


def test_two_navigation_entries_only(window) -> None:
    widget, _application = window
    assert set(widget._nav._buttons) == {"home", "settings"}


# ---------------------------------------------------------------------------
# Ce que l'écran ne doit jamais affirmer
# ---------------------------------------------------------------------------

def _status(circuit: CircuitId, *, state: ValveState, certain: bool,
            feedback: bool, commanded=ValveCommand.OPEN) -> CircuitStatus:
    return CircuitStatus(
        circuit=circuit, label="Local eau", mode=HeatingMode.MANUEL,
        zone=ZoneId.LOCAL_EAU, temperature_c=6.0,
        commanded=commanded,
        confirmed=ConfirmedState.OUVERT if certain else ConfirmedState.INCONNU,
        feedback_available=feedback, display_state=state,
        state_is_certain=certain,
        on_sensor_loss=SensorLossFallback.OPEN,
    )


def test_ouverte_and_fermee_are_reserved_to_confirmed_positions() -> None:
    """« OUVERTE » et « FERMÉE » décrivent une position, jamais une commande."""
    opened = _valve_appearance(_status(CircuitId.LOCAL_EAU, state=ValveState.OUVERT,
                                       certain=True, feedback=True))
    assert opened[3] == "OUVERTE"
    assert opened[1] is True                # corps plein = confirmé

    closed = _valve_appearance(_status(CircuitId.LOCAL_EAU, state=ValveState.FERME,
                                       certain=True, feedback=True,
                                       commanded=ValveCommand.CLOSE))
    assert closed[3] == "FERMÉE"
    assert closed[1] is True


def test_without_feedback_the_wording_names_the_command_not_a_position() -> None:
    """Sans retour de position, on n'annonce jamais qu'une vanne *est* ouverte."""
    for commanded, expected in (
        (ValveCommand.OPEN, "OUVERTURE COMMANDÉE"),
        (ValveCommand.CLOSE, "FERMETURE COMMANDÉE"),
    ):
        state = (ValveState.OUVERT if commanded is ValveCommand.OPEN
                 else ValveState.FERME)
        colour, filled, _crossed, text = _valve_appearance(
            _status(CircuitId.CABINE, state=state, certain=False,
                    feedback=False, commanded=commanded)
        )
        assert text == expected
        assert filled is False              # corps évidé = seulement commandé
        assert colour != "#3ED860"          # jamais la couleur d'un état confirmé
        assert "OUVERTE" not in text and "FERMÉE" not in text


def test_no_command_yet_reads_unknown() -> None:
    colour, filled, _crossed, text = _valve_appearance(
        _status(CircuitId.CABINE, state=ValveState.INCONNU, certain=False,
                feedback=False, commanded=ValveCommand.NONE)
    )
    assert text == "INCONNU"
    assert filled is False


def test_home_shows_the_commanded_wording(window) -> None:
    widget, application = window
    application.hal.sim_state.set_valve_feedback(CircuitId.CABINE, False)
    application.hal.sim_state.set_valve_travel_time(CircuitId.CABINE, 0.0)

    from vanmonitor.core.commands import ManualValveCommand
    application.command_bus.submit(
        ManualValveCommand(circuit=CircuitId.CABINE, action=ValveCommand.OPEN)
    )

    import time
    deadline = time.monotonic() + 3.0
    texts: list[str] = []
    while time.monotonic() < deadline:
        application.control.tick()
        widget.refresh()
        texts = _texts(widget._home)
        if "OUVERTURE COMMANDÉE" in texts:
            break
        time.sleep(0.05)

    assert "OUVERTURE COMMANDÉE" in texts, (
        "un état non confirmé doit être annoncé comme une commande"
    )
    assert "OUVERTE" not in texts, (
        "aucune vanne n'est confirmée ouverte dans cette configuration"
    )


def test_absent_sensor_shows_dashes_not_an_error(window) -> None:
    widget, _application = window
    snapshot = SystemSnapshot(
        timestamp=0.0,
        temperatures={
            ZoneId.COFFRE: TemperatureReading(
                zone=ZoneId.COFFRE, label="Coffre", celsius=None,
                status=Status.ABSENT, updated_at=None,
            ),
            ZoneId.CELLULE: TemperatureReading(
                zone=ZoneId.CELLULE, label="Cellule", celsius=None,
                status=Status.FAULT, updated_at=None, reason="erreur de lecture",
            ),
        },
    )
    widget._home.update_snapshot(snapshot)
    texts = _texts(widget._home)
    assert "--" in texts
    assert "Erreur capteur" in texts


def test_uncalibrated_tank_says_so_rather_than_inventing_litres(window) -> None:
    widget, _application = window
    snapshot = SystemSnapshot(
        timestamp=0.0,
        tanks={
            TankId.EAU_PROPRE: TankReading(
                tank=TankId.EAU_PROPRE, label="Eau propre", litres=None,
                percent=None, raw=0.5, status=Status.OK, calibrated=False,
            ),
        },
        battery=BatteryReading(status=Status.ABSENT),
    )
    widget._home.update_snapshot(snapshot)
    assert "Non calibré" in _texts(widget._home)


def test_missing_autonomy_hides_the_line_rather_than_showing_na(window) -> None:
    widget, _application = window
    reading = BatteryReading(
        soc_percent=87.0, voltage_v=13.2, current_a=-4.2, power_w=-55.0,
        consumed_ah=-12.0, time_to_go_min=None, status=Status.OK, updated_at=0.0,
    )
    widget._home._battery.update_reading(reading)
    assert widget._home._battery._autonomy.isVisible() is False


def test_a_stale_battery_never_leaves_an_old_autonomy_on_screen(window) -> None:
    """Après coupure, l'écran dit que le shunt ne répond plus — et rien d'autre."""
    widget, _application = window
    card = widget._home._battery

    card.update_reading(BatteryReading(
        soc_percent=87.0, voltage_v=13.2, current_a=-4.2, power_w=-55.0,
        consumed_ah=-12.0, time_to_go_min=1080, status=Status.OK, updated_at=0.0,
    ))
    assert "Autonomie : 18 h 00" in _texts(card)

    card.update_reading(BatteryReading(status=Status.STALE,
                                       reason="aucune lecture depuis 300 s"))
    texts = _texts(card)
    assert not any(text.startswith("Autonomie") for text in texts), (
        "aucune autonomie mémorisée ne doit rester affichée"
    )
    assert "SmartShunt non joignable" in texts
    assert "--" in texts


# ---------------------------------------------------------------------------
# Profils de disposition
# ---------------------------------------------------------------------------

def test_layout_profiles_switch_on_width() -> None:
    assert profile_for(800, 480) is LayoutProfile.STANDARD
    assert profile_for(1024, 600) is LayoutProfile.STANDARD
    assert profile_for(480, 272) is LayoutProfile.COMPACT


def test_metrics_scale_with_the_panel() -> None:
    standard = metrics_for(800, 480)
    compact = metrics_for(480, 272)
    assert compact.font_huge < standard.font_huge
    assert compact.px(10) >= 1          # rien ne tombe jamais à zéro


@pytest.mark.parametrize("size", [(800, 480), (1024, 600), (480, 272)])
def test_no_horizontal_overflow_at_any_supported_size(qt_app, tmp_path, size) -> None:
    """Contrainte ferme : jamais de défilement horizontal."""
    options = Options(
        config_path=tmp_path / f"config-{size[0]}.json", simulation=True,
        windowed=True, headless=False, duration_s=None, log_level="WARNING",
        screen_size=size, no_sim_panel=True,
    )
    application = build_application(options)
    application.start()
    from vanmonitor.ui.main_window import MainWindow
    widget = MainWindow(application)
    widget.show()
    qt_app.processEvents()
    try:
        assert widget.minimumSizeHint().width() <= size[0], (
            f"l'écran déborde en largeur à {size[0]}×{size[1]}"
        )
        widget.show_settings("heating")
        qt_app.processEvents()
        assert widget.minimumSizeHint().width() <= size[0]
    finally:
        widget.close()
        application.stop()
