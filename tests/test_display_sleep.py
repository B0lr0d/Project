"""Veille de l'écran : ce qui s'éteint, et surtout ce qui ne s'éteint pas.

La veille d'un tableau de bord embarqué n'est pas une économie d'énergie comme
une autre : si elle arrêtait la surveillance, elle transformerait un système de
sécurité en veilleuse. Les tests ci-dessous vérifient donc deux familles de
promesses.

D'abord la règle d'extinction elle-même : elle part au bon moment, ne part pas
quand elle est désactivée, repart de zéro à la moindre interaction, et suit le
délai choisi dans les Paramètres.

Ensuite, et c'est le cœur du sujet, ce que la veille laisse intact : les
acquisitions continuent, la chaîne du chauffage continue, les alertes continuent
d'être évaluées, et le premier doigt posé sur un écran noir ne fait que le
rallumer — il ne peut pas ouvrir un clapet qu'on ne voyait pas.

Les délais sont pilotés par une horloge explicite (``now=``) plutôt que par des
attentes réelles : un test de veille de cinq minutes ne doit pas durer cinq
minutes.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from vanmonitor.app import build_application
from vanmonitor.cli import Options
from vanmonitor.config import ConfigStore
from vanmonitor.constants import CircuitId, Status, TankId, ValveCommand, ZoneId
from vanmonitor.core.commands import ManualValveCommand
from vanmonitor.core.display import SLEEP_DELAYS_S, DisplayController
from vanmonitor.hal.sim.mock_display_power import MockDisplayPower


# ---------------------------------------------------------------------------
# Outillage
# ---------------------------------------------------------------------------

def _config(tmp_path: Path, **sections: dict) -> ConfigStore:
    path = tmp_path / "config.json"
    if sections:
        path.write_text(json.dumps(sections), encoding="utf-8")
    store = ConfigStore(path)
    store.load()
    return store


def _controller(tmp_path: Path, **sections: dict) -> tuple[DisplayController,
                                                           MockDisplayPower]:
    power = MockDisplayPower()
    return DisplayController(power, _config(tmp_path, **sections)), power


class _SynchronousWorker:
    """Le thread de veille, réduit à son effet observable.

    Le vrai worker réveille l'écran dès qu'on le lui demande ; ici le réveil a
    lieu tout de suite, ce qui rend les tests déterministes sans changer ce que
    l'interface voit.
    """

    def __init__(self, controller: DisplayController) -> None:
        self._controller = controller
        self.wake_requests = 0

    def request_wake(self) -> None:
        self.wake_requests += 1
        self._controller.wake()


def _wait_until(predicate, timeout_s: float = 10.0, step_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step_s)
    return False


# ---------------------------------------------------------------------------
# 1 — la veille se déclenche après le délai configuré
# ---------------------------------------------------------------------------

def test_the_screen_sleeps_after_the_configured_delay(tmp_path: Path) -> None:
    controller, power = _controller(tmp_path)
    assert controller.delay_s == 300.0          # 5 min, la valeur par défaut

    controller.note_activity(now=0.0)

    # Une seconde avant l'échéance, l'écran est toujours allumé.
    assert controller.tick(now=299.0) is False
    assert controller.is_asleep is False
    assert power.is_off is False

    assert controller.tick(now=300.0) is True
    assert controller.is_asleep is True
    assert power.is_off is True
    assert power.sleep_calls == 1

    # Et l'on n'éteint pas un écran déjà éteint à chaque tour de boucle.
    assert controller.tick(now=400.0) is False
    assert power.sleep_calls == 1


# ---------------------------------------------------------------------------
# 2 — veille désactivée : l'écran ne s'éteint jamais
# ---------------------------------------------------------------------------

def test_no_sleep_when_the_setting_is_disabled(tmp_path: Path) -> None:
    controller, power = _controller(
        tmp_path, display={"sleep_enabled": False, "sleep_delay_s": 60},
    )
    assert controller.enabled is False

    controller.note_activity(now=0.0)
    for instant in (60.0, 300.0, 3600.0, 86400.0):
        assert controller.tick(now=instant) is False

    assert controller.is_asleep is False
    assert power.sleep_calls == 0


def test_disabling_the_setting_wakes_a_sleeping_screen(tmp_path: Path) -> None:
    """Désactiver la veille pendant qu'elle dort doit rallumer, pas figer."""
    controller, power = _controller(tmp_path, display={"sleep_delay_s": 60})
    controller.note_activity(now=0.0)
    assert controller.tick(now=60.0) is True
    assert power.is_off is True

    controller._config.set("display.sleep_enabled", False)
    assert controller.tick(now=61.0) is True
    assert controller.is_asleep is False
    assert power.is_off is False


# ---------------------------------------------------------------------------
# 3 — toute interaction remet le compteur à zéro
# ---------------------------------------------------------------------------

def test_any_interaction_resets_the_inactivity_counter(tmp_path: Path) -> None:
    controller, power = _controller(tmp_path, display={"sleep_delay_s": 60})

    controller.note_activity(now=0.0)
    assert controller.tick(now=59.0) is False

    controller.note_activity(now=59.0)          # un doigt, à une seconde près
    assert controller.idle_seconds(now=59.0) == pytest.approx(0.0)

    # Le compte à rebours est reparti de 59 s, pas de 0 s.
    assert controller.tick(now=118.0) is False
    assert power.sleep_calls == 0
    assert controller.tick(now=119.0) is True
    assert power.is_off is True


def test_the_wake_guard_reports_every_interaction(tmp_path: Path) -> None:
    """Le filtre d'événements repousse la veille sans rien intercepter."""
    pytest.importorskip("PyQt5", reason="interface graphique non installée")
    from PyQt5.QtCore import QEvent, QObject

    from vanmonitor.ui.wake_guard import WakeGuard

    controller, _power = _controller(tmp_path)
    guard = WakeGuard(controller, _SynchronousWorker(controller))

    controller.note_activity(now=0.0)
    # Un événement d'interface quelconque, écran allumé : laissé passer.
    handled = guard.eventFilter(QObject(), QEvent(QEvent.MouseButtonPress))
    assert handled is False
    assert controller.idle_seconds() < 1.0


# ---------------------------------------------------------------------------
# 4 et 5 — le premier toucher réveille, et ne fait que cela
# ---------------------------------------------------------------------------

@pytest.fixture()
def qt_app():
    pytest.importorskip("PyQt5", reason="interface graphique non installée")
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _press_and_release(qt_app, widget) -> None:
    """Un appui complet, envoyé comme le ferait la dalle tactile."""
    from PyQt5.QtCore import QEvent, QPointF, Qt
    from PyQt5.QtGui import QMouseEvent

    for kind in (QEvent.MouseButtonPress, QEvent.MouseButtonRelease):
        qt_app.sendEvent(widget, QMouseEvent(
            kind, QPointF(10.0, 10.0), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
        ))


def test_the_first_touch_on_a_dark_screen_only_wakes(qt_app, tmp_path: Path) -> None:
    from PyQt5.QtWidgets import QPushButton

    from vanmonitor.ui.wake_guard import WakeGuard

    controller, power = _controller(tmp_path, display={"sleep_delay_s": 60})
    worker = _SynchronousWorker(controller)
    guard = WakeGuard(controller, worker)
    qt_app.installEventFilter(guard)

    button = QPushButton("OUVRIR")
    button.resize(120, 60)

    try:
        controller.note_activity(now=0.0)
        assert controller.tick(now=60.0) is True
        assert power.is_off is True

        _press_and_release(qt_app, button)

        assert worker.wake_requests == 1
        assert controller.is_asleep is False
        assert power.wake_calls == 1
        # Le geste entier a été absorbé : l'appui et le relâchement.
        assert guard.swallowed_events == 2
        assert guard.is_swallowing is False
    finally:
        qt_app.removeEventFilter(guard)


def test_the_waking_touch_triggers_no_command(qt_app, tmp_path: Path) -> None:
    """Le doigt qui rallume ne doit ouvrir aucun clapet, ni changer de page.

    C'est la raison d'être de la règle : sur un écran noir, on ne voit pas ce
    qui se trouve sous le doigt.
    """
    from PyQt5.QtWidgets import QPushButton

    from vanmonitor.ui.wake_guard import WakeGuard

    controller, _power = _controller(tmp_path, display={"sleep_delay_s": 60})
    guard = WakeGuard(controller, _SynchronousWorker(controller))
    qt_app.installEventFilter(guard)

    clicks: list[str] = []
    button = QPushButton("OUVRIR")
    button.resize(120, 60)
    button.clicked.connect(lambda: clicks.append("ouvrir"))

    try:
        controller.note_activity(now=0.0)
        controller.tick(now=60.0)
        assert controller.is_asleep is True

        _press_and_release(qt_app, button)
        assert clicks == [], "le toucher de réveil a déclenché la commande sous le doigt"

        # Et juste après, l'interface est normalement utilisable : le deuxième
        # appui, lui, agit.
        _press_and_release(qt_app, button)
        assert clicks == ["ouvrir"]
    finally:
        qt_app.removeEventFilter(guard)


# ---------------------------------------------------------------------------
# Le geste de réveil, quel que soit le flux par lequel la dalle l'expose
# ---------------------------------------------------------------------------
#
# Une dalle tactile USB n'arrive pas toujours à Qt de la même façon : tactile
# natif, souris synthétisée, ou les deux à la fois. Le programme ne choisit pas
# — cela dépend du greffon de plateforme et de la pile d'entrée du système.
# La règle doit donc tenir dans les trois cas, et « tenir » veut dire deux
# choses : le réveil est demandé, et **aucun événement n'atteint le widget**.
#
# Le réveil est ici **asynchrone**, comme le vrai ``DisplayWorker`` : la fin du
# geste arrive avec l'écran encore marqué endormi.


class _DeferredWorker:
    """Le thread de veille avec sa latence réelle.

    Le vrai worker tourne dans un autre thread : entre la demande de réveil et
    l'extinction effective du drapeau, les événements suivants du geste
    continuent d'arriver. C'est le cas qui compte, et celui qu'un faux worker
    synchrone masquerait.
    """

    def __init__(self, controller: DisplayController) -> None:
        self._controller = controller
        self.wake_requests = 0

    def request_wake(self) -> None:
        self.wake_requests += 1

    def run_pending(self) -> None:
        """Ce que le thread finit par faire, quand on décide qu'il l'a fait."""
        if self.wake_requests:
            self._controller.wake()


def _recorder(widget):
    """Espion posé sur le widget : note tout ce qui parvient jusqu'à lui."""
    from PyQt5.QtCore import QEvent, QObject

    class _Spy(QObject):
        def __init__(self) -> None:
            super().__init__()
            self.seen: list[QEvent.Type] = []

        def eventFilter(self, watched, event):     # noqa: N802 (API Qt)
            self.seen.append(event.type())
            return False

    spy = _Spy()
    widget.installEventFilter(spy)
    return spy


def _mouse(kind):
    from PyQt5.QtCore import QPointF, Qt
    from PyQt5.QtGui import QMouseEvent
    return QMouseEvent(kind, QPointF(10.0, 10.0), Qt.LeftButton,
                       Qt.LeftButton, Qt.NoModifier)


def _touch(kind):
    """Un événement tactile natif.

    Sans point de contact : PyQt5 ne permet pas de construire un
    ``QTouchEvent.TouchPoint``. Ce n'en est pas moins un vrai ``QTouchEvent``,
    du bon type, et c'est le type que le filtre examine.
    """
    from PyQt5.QtCore import QEvent, Qt
    from PyQt5.QtGui import QTouchDevice, QTouchEvent

    device = QTouchDevice()
    device.setType(QTouchDevice.TouchScreen)
    state = {
        QEvent.TouchBegin: Qt.TouchPointPressed,
        QEvent.TouchUpdate: Qt.TouchPointMoved,
        QEvent.TouchEnd: Qt.TouchPointReleased,
    }[kind]
    return QTouchEvent(kind, device, Qt.NoModifier, state, [])


def _gesture(qt_app, guard, widget, events) -> None:
    """Rejoue un geste comme Qt le ferait, filtre de l'application compris.

    Qt consulte les filtres posés sur le ``QApplication`` **avant** ceux de
    l'objet destinataire et avant ``event()`` : un filtre qui retourne ``True``
    met fin à l'acheminement, et l'événement n'atteint personne. C'est
    exactement ce que l'on reproduit ici — le filtre décide, et seul ce qu'il
    laisse passer est réellement remis au widget.

    Ce détour est nécessaire pour le flux tactile : ``QApplication.notify``
    aiguille un ``QTouchEvent`` d'après ses **points de contact**, que PyQt5 ne
    sait pas construire. Un événement tactile envoyé depuis un test n'atteint
    donc jamais la boucle de distribution. Le flux souris, lui, est éprouvé de
    bout en bout avec le filtre réellement installé sur le ``QApplication``
    (voir ``test_the_waking_touch_triggers_no_command``).
    """
    for event in events:
        if not guard.eventFilter(widget, event):
            qt_app.sendEvent(widget, event)


@pytest.fixture()
def dark_screen(qt_app, tmp_path: Path):
    """Un bouton sous un écran éteint, et de quoi observer ce qui l'atteint."""
    from PyQt5.QtWidgets import QPushButton

    from vanmonitor.ui.wake_guard import WakeGuard

    controller, power = _controller(tmp_path, display={"sleep_delay_s": 60})
    worker = _DeferredWorker(controller)
    guard = WakeGuard(controller, worker)

    clicks: list[str] = []
    button = QPushButton("OUVRIR")
    button.resize(120, 60)
    button.clicked.connect(lambda: clicks.append("ouvrir"))
    spy = _recorder(button)

    controller.note_activity(now=0.0)
    assert controller.tick(now=60.0) is True
    assert power.is_off is True

    yield button, guard, worker, controller, clicks, spy


def _assert_woken_without_reaching_the_widget(guard, worker, controller,
                                              clicks, spy, expected: int) -> None:
    assert worker.wake_requests >= 1, "le geste n'a pas demandé le réveil"
    worker.run_pending()
    assert controller.is_asleep is False
    assert spy.seen == [], (
        "des événements ont atteint le widget sous le doigt : "
        f"{[int(kind) for kind in spy.seen]}"
    )
    assert clicks == []
    assert guard.swallowed_events == expected
    assert guard.is_swallowing is False, "le filtre avale encore après le geste"


def test_a_native_touch_gesture_is_fully_swallowed(qt_app, dark_screen) -> None:
    """Flux 1 : événements tactiles natifs."""
    from PyQt5.QtCore import QEvent

    button, guard, worker, controller, clicks, spy = dark_screen
    _gesture(qt_app, guard, button, [
        _touch(QEvent.TouchBegin),
        _touch(QEvent.TouchUpdate),
        _touch(QEvent.TouchUpdate),
        _touch(QEvent.TouchEnd),
    ])
    _assert_woken_without_reaching_the_widget(
        guard, worker, controller, clicks, spy, expected=4,
    )


def test_a_synthesized_mouse_gesture_is_fully_swallowed(qt_app, dark_screen) -> None:
    """Flux 2 : la dalle est exposée comme un pointeur, appui / déplacement /
    relâchement — c'est le cas le plus courant sur Raspberry Pi OS."""
    from PyQt5.QtCore import QEvent

    button, guard, worker, controller, clicks, spy = dark_screen
    _gesture(qt_app, guard, button, [
        _mouse(QEvent.MouseButtonPress),
        _mouse(QEvent.MouseMove),
        _mouse(QEvent.MouseMove),
        _mouse(QEvent.MouseButtonRelease),
    ])
    _assert_woken_without_reaching_the_widget(
        guard, worker, controller, clicks, spy, expected=4,
    )


def test_an_interleaved_touch_and_mouse_gesture_is_fully_swallowed(
    qt_app, dark_screen,
) -> None:
    """Flux 3 : tactile natif **doublé** d'une souris synthétisée.

    Le relâchement souris arrive après le ``TouchEnd``. Refermer le geste sur
    le premier relâchement venu le laisserait passer : c'est pour ce cas que le
    filtre suit les deux flux séparément.
    """
    from PyQt5.QtCore import QEvent

    button, guard, worker, controller, clicks, spy = dark_screen
    _gesture(qt_app, guard, button, [
        _touch(QEvent.TouchBegin),
        _mouse(QEvent.MouseButtonPress),
        _touch(QEvent.TouchUpdate),
        _mouse(QEvent.MouseMove),
        _touch(QEvent.TouchEnd),
        _mouse(QEvent.MouseButtonRelease),
    ])
    _assert_woken_without_reaching_the_widget(
        guard, worker, controller, clicks, spy, expected=6,
    )


def test_an_interrupted_gesture_never_blocks_the_interface(qt_app, dark_screen,
                                                           monkeypatch) -> None:
    """Un doigt dont le relâchement n'arrive jamais ne fige pas l'écran.

    Doigt resté posé, événement perdu, geste annulé par le système : au bout de
    ``MAX_SWALLOW_S`` le filtre cesse d'absorber, plutôt que de laisser une
    interface inerte.
    """
    from PyQt5.QtCore import QEvent

    from vanmonitor.ui import wake_guard

    button, guard, worker, controller, clicks, spy = dark_screen

    clock = {"now": 1000.0}
    monkeypatch.setattr(wake_guard, "monotonic", lambda: clock["now"])

    # Le doigt se pose… et ne se relève jamais.
    _gesture(qt_app, guard, button, [_touch(QEvent.TouchBegin)])
    assert guard.is_swallowing is True
    worker.run_pending()

    clock["now"] += wake_guard.MAX_SWALLOW_S + 1.0
    _gesture(qt_app, guard, button, [_touch(QEvent.TouchUpdate)])

    assert guard.is_swallowing is False
    assert spy.seen == []
    _gesture(qt_app, guard, button, [_mouse(QEvent.MouseButtonPress),
                              _mouse(QEvent.MouseButtonRelease)])
    assert clicks == ["ouvrir"], "l'interface est restée inerte après le garde-fou"


def test_the_tap_after_an_asynchronous_wake_is_not_eaten(qt_app, dark_screen) -> None:
    """Le geste de réveil terminé, l'appui suivant agit — même si le réveil a
    pris du temps.

    La fin du geste de réveil arrive alors que l'écran est encore marqué
    endormi. Rouvrir une absorption à ce moment-là, sans jamais la refermer,
    ferait avaler le **deuxième** appui : celui que l'utilisateur veut voir agir.
    """
    from PyQt5.QtCore import QEvent

    button, guard, worker, controller, clicks, spy = dark_screen

    # Tout le geste de réveil arrive avant que le thread de veille n'ait agi.
    _gesture(qt_app, guard, button, [_mouse(QEvent.MouseButtonPress),
                              _mouse(QEvent.MouseButtonRelease)])
    assert controller.is_asleep is True, "le réveil n'est plus asynchrone"
    assert clicks == []
    assert guard.is_swallowing is False, "le geste de réveil n'a pas été refermé"

    worker.run_pending()
    assert controller.is_asleep is False

    _gesture(qt_app, guard, button, [_mouse(QEvent.MouseButtonPress),
                              _mouse(QEvent.MouseButtonRelease)])
    assert clicks == ["ouvrir"], "le premier appui utile a été avalé"


# ---------------------------------------------------------------------------
# 6, 7, 8 — le Raspberry, lui, ne dort pas
# ---------------------------------------------------------------------------

@pytest.fixture()
def sleeping_application(tmp_path: Path):
    """Une installation simulée complète, écran éteint, tout le reste en marche."""
    (tmp_path / "config.json").write_text(json.dumps({
        "temperatures": {"poll_period_s": 1},
        "alerts": {"min_duration_s": 0},
        "display": {"sleep_delay_s": 60},
    }), encoding="utf-8")

    application = build_application(Options(
        config_path=tmp_path / "config.json", simulation=True, windowed=True,
        headless=True, duration_s=None, log_level="WARNING",
        screen_size=(800, 480), no_sim_panel=True,
    ))
    application.start()

    application.display.note_activity(now=0.0)
    assert application.display.tick(now=60.0) is True
    assert application.hal.display_power.is_off is True

    try:
        yield application
    finally:
        application.stop()


def test_acquisitions_continue_while_the_screen_sleeps(sleeping_application) -> None:
    application = sleeping_application
    before = application.acquisition.snapshot()

    def _newer(current: float | None, previous: float | None) -> bool:
        return current is not None and current > (previous or 0.0)

    def _every_family_read_again() -> bool:
        now = application.acquisition.snapshot()
        return (
            _newer(now.temperatures[ZoneId.CELLULE].updated_at,
                   before.temperatures[ZoneId.CELLULE].updated_at)
            and _newer(now.levels[TankId.EAU_PROPRE].updated_at,
                       before.levels[TankId.EAU_PROPRE].updated_at)
            and _newer(now.battery.updated_at, before.battery.updated_at)
        )

    assert _wait_until(_every_family_read_again), \
        "une acquisition s'est arrêtée avec l'écran"

    after = application.acquisition.snapshot()
    assert after.temperatures[ZoneId.CELLULE].status is Status.OK
    assert after.levels[TankId.EAU_PROPRE].status is Status.OK
    assert after.battery.status is Status.OK
    # Et l'écran, lui, dort toujours : lire ne réveille pas.
    assert application.display.is_asleep is True


def test_the_heating_chain_keeps_working_while_the_screen_sleeps(
    sleeping_application,
) -> None:
    """Écran éteint, un ordre de chauffage doit toujours atteindre le clapet.

    La régulation automatique elle-même est l'étape 7 ; ce qui se vérifie ici
    est la chaîne qu'elle empruntera — file de commandes, thread des clapets,
    relecture de la position — et l'assemblage continu de l'état des circuits.
    """
    application = sleeping_application
    circuit = CircuitId.LOCAL_EAU

    ticks_before = application.control.ticks
    application.command_bus.submit(
        ManualValveCommand(circuit=circuit, action=ValveCommand.OPEN)
    )

    def _opened() -> bool:
        snapshot = application.control.tick()
        status = snapshot.circuits.get(circuit)
        return status is not None and status.commanded is ValveCommand.OPEN

    assert _wait_until(_opened), "le clapet n'a pas reçu l'ordre, écran éteint"

    snapshot = application.control.tick()
    assert application.control.ticks > ticks_before
    assert set(snapshot.circuits) == {
        CircuitId.LOCAL_EAU, CircuitId.LOCAL_BATTERIE, CircuitId.CABINE,
    }
    for status in snapshot.circuits.values():
        assert status.label not in {"Circuit 1", "Circuit 2", "Circuit 3"}
    assert application.display.is_asleep is True


def test_alerts_keep_being_evaluated_while_the_screen_sleeps(
    sleeping_application,
) -> None:
    application = sleeping_application
    application.hal.sim_state.update_battery(soc_percent=9.0)

    def _battery_alert() -> bool:
        snapshot = application.control.tick()
        return any(alert.key == "batterie_basse" for alert in snapshot.alerts)

    assert _wait_until(_battery_alert), "l'alerte batterie n'est plus évaluée"

    # L'alerte n'allume pas l'écran d'elle-même : la veille reste en place tant
    # qu'un doigt ne l'a pas levée.
    assert application.display.is_asleep is True
    snapshot = application.control.tick()
    assert snapshot.display is not None
    assert snapshot.display.asleep is True


# ---------------------------------------------------------------------------
# 9 — le délai choisi dans les Paramètres est bien celui appliqué
# ---------------------------------------------------------------------------

def test_the_delay_chosen_in_settings_is_applied(qt_app, tmp_path: Path) -> None:
    from vanmonitor.ui.settings.display_settings import DELAY_CHOICES, DisplaySettings
    from vanmonitor.ui.theme import metrics_for

    config = _config(tmp_path)
    power = MockDisplayPower()
    controller = DisplayController(power, config)

    # Les choix proposés sont exactement ceux demandés, dans cet ordre.
    assert [key for key, _text in DELAY_CHOICES] == [
        str(delay) for delay in SLEEP_DELAYS_S
    ]
    assert [text for _key, text in DELAY_CHOICES] == [
        "Désactivée", "1 min", "5 min", "10 min", "30 min",
    ]

    section = DisplaySettings(config, metrics_for(800, 480, 5.0))
    assert section._delay._current == "300"     # 5 min par défaut

    section._delay._choose("60")
    assert config.get("display.sleep_enabled") is True
    assert config.get("display.sleep_delay_s") == 60
    assert controller.delay_s == 60.0

    controller.note_activity(now=0.0)
    assert controller.tick(now=59.0) is False
    assert controller.tick(now=60.0) is True    # le nouveau délai, pas l'ancien

    controller.wake(now=60.0)
    section._delay._choose("1800")
    assert config.get("display.sleep_delay_s") == 1800
    assert controller.tick(now=1000.0) is False
    assert controller.tick(now=1860.0) is True

    controller.wake(now=1860.0)
    section._delay._choose("0")
    assert config.get("display.sleep_enabled") is False
    assert controller.enabled is False
    assert controller.tick(now=100000.0) is False
    assert power.is_off is False
    section.deleteLater()


# ---------------------------------------------------------------------------
# Ce que la veille fait quand elle n'y arrive pas
# ---------------------------------------------------------------------------

def test_a_failing_method_never_pretends_the_screen_is_off(tmp_path: Path) -> None:
    """Se croire endormi ferait avaler le prochain toucher pour rien."""
    power = MockDisplayPower(failing=True)
    controller = DisplayController(power, _config(tmp_path,
                                                  display={"sleep_delay_s": 60}))

    controller.note_activity(now=0.0)
    assert controller.tick(now=60.0) is False
    assert controller.is_asleep is False
    assert power.sleep_calls == 1

    status = controller.status(now=60.0)
    assert status.asleep is False
    assert status.last_error is not None


def test_an_unavailable_method_is_announced_rather_than_silent(tmp_path: Path) -> None:
    power = MockDisplayPower(available=False)
    controller = DisplayController(power, _config(tmp_path))
    assert controller.available is False
    assert controller.status().available is False
