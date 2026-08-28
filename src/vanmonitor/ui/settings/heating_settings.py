"""Section CHAUFFAGE : modes, seuils, commandes manuelles, repli de sécurité.

Le repli sur perte de sonde est le seul réglage de toute l'application qui ne
s'applique pas immédiatement : il ouvre une confirmation. C'est un choix qui
engage la protection contre le gel, il ne doit pas pouvoir changer d'un doigt
posé au mauvais endroit sur une route cabossée.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from ...constants import (
    CIRCUIT_ORDER,
    CircuitId,
    FALLBACK_LABELS,
    HeatingMode,
    SensorLossFallback,
    ValveCommand,
)
from ...core.commands import CommandBus, ManualValveCommand
from ...core.services import HeatingService
from ...models import CircuitStatus, SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.cards import _valve_appearance
from ..widgets.dialogs import ConfirmDialog, NumericKeypad, SegmentedControl
from ..widgets.primitives import Card, french, label, recolor
from . import SettingsSection, field_row, value_button

#: Conséquence annoncée avant confirmation, formulée pour chaque choix.
FALLBACK_CONSEQUENCES = {
    SensorLossFallback.OPEN: (
        "le circuit sera ouvert.", False,
    ),
    SensorLossFallback.CLOSE: (
        "le circuit sera fermé — la protection contre le gel ne sera plus assurée.",
        True,
    ),
    SensorLossFallback.HOLD: (
        "le circuit restera dans son dernier état.", False,
    ),
}


class CircuitSettings(Card):
    """Le bloc de réglages d'un circuit, nommé — jamais « Circuit 1 »."""

    def __init__(self, circuit: CircuitId, heating: HeatingService,
                 command_bus: CommandBus, metrics: Metrics,
                 parent: QWidget | None = None) -> None:
        super().__init__(heating.label(circuit), metrics, parent)
        self._circuit = circuit
        self._heating = heating
        self._bus = command_bus
        self._metrics = metrics

        # --- mode et état courant --------------------------------------
        top = QHBoxLayout()
        top.setSpacing(metrics.px(8))
        self._mode = SegmentedControl([("auto", "AUTO"), ("manuel", "MANUEL")], metrics)
        self._mode.on_change(self._set_mode)
        top.addWidget(self._mode, 3)
        self._state = label("--", size=metrics.font_small, color=theme.TEXT, bold=True,
                            align=Qt.AlignRight | Qt.AlignVCenter)
        top.addWidget(self._state, 2)
        self.body().addLayout(top)

        self._mode_note = label("", size=metrics.font_tiny, color=theme.AMBER)
        self.body().addWidget(self._mode_note)

        # --- seuils, sur une seule ligne -------------------------------
        thresholds = QHBoxLayout()
        thresholds.setSpacing(metrics.px(8))
        thresholds.addWidget(label("Ouverture", size=metrics.font_small,
                                   color=theme.TEXT_MUTED))
        self._open = value_button("--", metrics)
        self._open.clicked.connect(lambda: self._edit_threshold(opening=True))
        thresholds.addWidget(self._open)
        thresholds.addStretch(1)
        thresholds.addWidget(label("Fermeture", size=metrics.font_small,
                                   color=theme.TEXT_MUTED))
        self._close = value_button("--", metrics)
        self._close.clicked.connect(lambda: self._edit_threshold(opening=False))
        thresholds.addWidget(self._close)
        self.body().addLayout(thresholds)

        # --- commandes manuelles ---------------------------------------
        commands = QHBoxLayout()
        commands.setSpacing(metrics.px(6))
        self._open_button = QPushButton("OUVRIR")
        self._open_button.setMinimumHeight(metrics.touch_min)
        self._open_button.clicked.connect(lambda: self._command(ValveCommand.OPEN))
        self._close_button = QPushButton("FERMER")
        self._close_button.setMinimumHeight(metrics.touch_min)
        self._close_button.clicked.connect(lambda: self._command(ValveCommand.CLOSE))
        commands.addWidget(self._open_button, 1)
        commands.addWidget(self._close_button, 1)
        self.body().addLayout(commands)

        # --- repli de sécurité -----------------------------------------
        warning = label("⚠  Repli si la sonde ne répond plus",
                        size=metrics.font_small, color=theme.AMBER, bold=True)
        self.body().addWidget(warning)

        self._fallback = SegmentedControl(
            [("open", "OUVRIR"), ("close", "FERMER"), ("hold", "MAINTENIR")], metrics
        )
        self._fallback.on_change(self._set_fallback)
        self.body().addWidget(self._fallback)

        self._fallback_note = label("", size=metrics.font_tiny, color=theme.TEXT_MUTED)
        self._fallback_note.setWordWrap(True)
        self.body().addWidget(self._fallback_note)

        self.reload()

    # ------------------------------------------------------------------
    def reload(self) -> None:
        circuit = self._circuit
        low, high = self._heating.thresholds(circuit)
        defined = self._heating.thresholds_defined(circuit)

        self._open.setText("--" if low is None else f"{french(low)} °C")
        self._close.setText("--" if high is None else f"{french(high)} °C")

        self._mode.set_current(self._heating.mode(circuit).value)
        self._mode.set_enabled_keys({"auto", "manuel"} if defined else {"manuel"})
        note = "" if defined else "Seuils à définir — mode AUTO indisponible"
        self._mode_note.setText(note)
        self._mode_note.setVisible(bool(note))

        fallback = self._heating.fallback(circuit)
        self._fallback.set_current(fallback.value)
        consequence, _severe = FALLBACK_CONSEQUENCES[fallback]
        self._fallback_note.setText(f"Si la sonde tombe, {consequence}")

        manual = self._heating.mode(circuit) is HeatingMode.MANUEL
        self._open_button.setEnabled(manual)
        self._close_button.setEnabled(manual)

    def update_status(self, status: CircuitStatus) -> None:
        colour, _filled, _crossed, text = _valve_appearance(status)
        if not status.state_is_certain and status.commanded is not ValveCommand.NONE:
            text = f"{text}  (commandé)"
        self._state.setText(text)
        recolor(self._state, colour)

    # ------------------------------------------------------------------
    def _set_mode(self, key: str) -> bool:
        mode = HeatingMode.AUTO if key == "auto" else HeatingMode.MANUEL
        if not self._heating.set_mode(self._circuit, mode):
            return False        # seuils non définis : le bouton ne bouge pas
        self.reload()
        return True

    def _edit_threshold(self, *, opening: bool) -> None:
        low, high = self._heating.thresholds(self._circuit)
        current = low if opening else high
        title = (f"{self._heating.label(self._circuit)} — "
                 f"seuil {'d’ouverture' if opening else 'de fermeture'}")
        dialog = NumericKeypad(title, current, self._metrics, unit="°C", decimals=1,
                               minimum=-40, maximum=85, parent=self)
        if not dialog.exec_() or dialog.value is None:
            return

        new_low = dialog.value if opening else (low if low is not None else dialog.value - 3)
        new_high = (high if high is not None else dialog.value + 3) if opening else dialog.value
        try:
            self._heating.set_thresholds(self._circuit, new_low, new_high)
        except ValueError as exc:
            self._mode_note.setText(str(exc))
            return
        self._mode_note.setText("")
        self.reload()

    def _command(self, action: ValveCommand) -> None:
        """Dépose l'ordre dans la file : l'écran ne pilote jamais un clapet."""
        self._bus.submit(ManualValveCommand(circuit=self._circuit, action=action))

    def _set_fallback(self, key: str) -> bool:
        target = SensorLossFallback(key)
        current = self._heating.fallback(self._circuit)
        if target is current:
            return True

        consequence, severe = FALLBACK_CONSEQUENCES[target]
        zone_label = self._heating.label(self._circuit)
        dialog = ConfirmDialog(
            "Réglage de sécurité",
            f"{zone_label} — repli si la sonde ne répond plus",
            f"{FALLBACK_LABELS[current]}   →   {FALLBACK_LABELS[target]}",
            f"Si la sonde {zone_label} cesse de répondre, {consequence}\n"
            "Ce choix conditionne la protection contre le gel.",
            self._metrics, severe=severe, parent=self,
        )
        if not dialog.exec_():
            return False        # annulé : le sélecteur reste où il était

        self._heating.set_fallback(self._circuit, target)
        self.reload()
        return True


class HeatingSettings(SettingsSection):
    """Un circuit à la fois : sur une dalle de 4,3 pouces, empiler les trois
    obligerait à faire défiler la page pour atteindre le dernier réglage."""

    def __init__(self, heating: HeatingService, command_bus: CommandBus,
                 metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(metrics, parent)

        self._selector = SegmentedControl(
            [(circuit.value, heating.label(circuit)) for circuit in CIRCUIT_ORDER],
            metrics,
        )
        self._selector.on_change(self._select)
        self.column().addWidget(self._selector)

        self._blocks: dict[CircuitId, CircuitSettings] = {}
        self._stack = QStackedWidget()
        for circuit in CIRCUIT_ORDER:
            block = CircuitSettings(circuit, heating, command_bus, metrics)
            self._blocks[circuit] = block
            self._stack.addWidget(block)
        self.column().addWidget(self._stack, 1)

        self._selector.set_current(CIRCUIT_ORDER[0].value)

    def _select(self, key: str) -> bool:
        self._stack.setCurrentWidget(self._blocks[CircuitId(key)])
        return True

    def refresh(self, snapshot: SystemSnapshot) -> None:
        for circuit, block in self._blocks.items():
            status = snapshot.circuits.get(circuit)
            if status is not None:
                block.update_status(status)
