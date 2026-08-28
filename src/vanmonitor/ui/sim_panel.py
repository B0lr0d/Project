"""Panneau de simulation — le tableau de bord du fourgon virtuel.

À gauche, le **monde simulé** : on y règle les températures, les niveaux, la
batterie, et surtout on y provoque des pannes. À droite, **ce que le logiciel
en perçoit** : c'est là que se vérifie le comportement réel du programme —
valeurs périmées, erreurs capteur, clapets dont l'état n'est pas confirmé.

Deux règles tenues ici :

* le panneau ne touche jamais un pilote. Pour ouvrir un clapet, il dépose une
  commande dans la file, exactement comme le fera l'écran Accueil ;
* il est le seul module d'interface autorisé à écrire dans ``hal.sim`` —
  c'est sa raison d'être, et le test des imports l'autorise nommément.
"""

from __future__ import annotations

from PyQt5.QtCore import QLocale, Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..constants import (
    CIRCUIT_ORDER,
    CircuitId,
    TANK_ORDER,
    TankId,
    ValveCommand,
    ZONE_ORDER,
    ZoneId,
)
from ..core.commands import ManualValveCommand
from ..hal.sim.sim_state import FAULT_LABELS, FaultMode, SimState
from .snapshot_text import (
    CIRCUIT_LABELS,
    TANK_LABELS,
    ZONE_LABELS,
    format_snapshot,
    fr,
)

# Palette : celle arrêtée pour l'application, pour que l'œil s'y habitue.
BACKGROUND = "#0E1116"
SURFACE = "#171B22"
TEXT = "#F2F5F9"
MUTED = "#8B96A5"
LINE = "#262D37"
ACCENT = "#F0883E"

STYLESHEET = f"""
QWidget {{ background: {BACKGROUND}; color: {TEXT}; font-size: 13px; }}
QGroupBox {{
    background: {SURFACE}; border: 1px solid {LINE}; border-radius: 6px;
    margin-top: 14px; padding: 10px 8px 8px 8px; font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 10px; padding: 0 6px;
    color: {MUTED}; text-transform: uppercase;
}}
QLabel#value {{ color: {TEXT}; font-family: monospace; }}
QLabel#muted {{ color: {MUTED}; }}
QComboBox, QDoubleSpinBox {{
    background: {BACKGROUND}; border: 1px solid {LINE};
    border-radius: 4px; padding: 3px 6px; min-height: 22px;
}}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {LINE};
    border-radius: 3px; background: {BACKGROUND};
}}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; selection-background-color: {ACCENT}; }}
QPushButton {{
    background: {BACKGROUND}; border: 1px solid {LINE};
    border-radius: 4px; padding: 6px 12px; min-height: 24px;
}}
QPushButton:hover {{ border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT}; color: {BACKGROUND}; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: {ACCENT}; width: 14px; margin: -6px 0; border-radius: 7px;
}}
QProgressBar {{
    background: {LINE}; border: none; border-radius: 3px;
    height: 8px; text-align: center; color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QTextEdit {{
    background: {BACKGROUND}; border: 1px solid {LINE}; border-radius: 6px;
    font-family: monospace; font-size: 12px;
}}
"""


#: Virgule décimale partout, y compris dans les champs de saisie.
FRENCH = QLocale(QLocale.French, QLocale.France)


def _fault_combo() -> QComboBox:
    combo = QComboBox()
    for mode in FaultMode:
        combo.addItem(FAULT_LABELS[mode], mode)
    return combo


def _spin(
    minimum: float, maximum: float, decimals: int, step: float, suffix: str, value: float,
) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setLocale(FRENCH)
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    spin.setSuffix(suffix)
    spin.setValue(value)
    return spin


class SimulationPanel(QWidget):
    """Fenêtre unique du mode simulation."""

    def __init__(self, application) -> None:
        super().__init__()
        self._app = application
        self._sim: SimState = application.hal.sim_state
        if self._sim is None:      # pragma: no cover - garde-fou
            raise RuntimeError("le panneau de simulation exige le matériel simulé")

        self.setWindowTitle("Fourgon — panneau de simulation (étape 2)")
        self.setStyleSheet(STYLESHEET)
        self.resize(1180, 760)

        self._temperature_labels: dict[ZoneId, QLabel] = {}
        self._level_labels: dict[TankId, QLabel] = {}
        self._valve_bars: dict[CircuitId, QProgressBar] = {}

        self._build_ui()
        self._start_refresh()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._build_temperatures())
        left_layout.addWidget(self._build_levels())
        left_layout.addWidget(self._build_battery())
        left_layout.addWidget(self._build_heating())
        left_layout.addWidget(self._build_actions())
        left_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setFrameStyle(0)
        scroll.setMinimumWidth(560)

        self._readout = QTextEdit()
        self._readout.setReadOnly(True)
        self._readout.setLineWrapMode(QTextEdit.NoWrap)
        self._readout.setFont(QFont("monospace", 10))

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(4, 4, 4, 4)
        title = QLabel("CE QUE LE LOGICIEL PERÇOIT")
        title.setObjectName("muted")
        right_layout.addWidget(title)
        right_layout.addWidget(self._readout, 1)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(12)
        layout.addWidget(scroll, 0)
        layout.addWidget(right, 1)

    # -- températures ---------------------------------------------------
    def _build_temperatures(self) -> QGroupBox:
        box = QGroupBox("Températures")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        for row, zone in enumerate(ZONE_ORDER):
            grid.addWidget(QLabel(ZONE_LABELS[zone.value]), row, 0)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(-300, 500)          # -30,0 à 50,0 °C au dixième
            slider.setValue(int(self._sim.temperature(zone) * 10))
            grid.addWidget(slider, row, 1)

            value = QLabel()
            value.setObjectName("value")
            value.setMinimumWidth(64)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(value, row, 2)
            self._temperature_labels[zone] = value

            combo = _fault_combo()
            combo.setMinimumWidth(180)
            grid.addWidget(combo, row, 3)

            slider.valueChanged.connect(
                lambda raw, zone=zone: self._on_temperature(zone, raw)
            )
            combo.currentIndexChanged.connect(
                lambda index, zone=zone, combo=combo:
                self._sim.set_temperature_fault(zone, combo.itemData(index))
            )
            self._on_temperature(zone, slider.value())

        return box

    def _on_temperature(self, zone: ZoneId, raw: int) -> None:
        celsius = raw / 10.0
        self._sim.set_temperature(zone, celsius)
        self._temperature_labels[zone].setText(f"{fr(celsius)} °C")

    # -- niveaux --------------------------------------------------------
    def _build_levels(self) -> QGroupBox:
        box = QGroupBox("Niveaux  (valeur brute du capteur, sans unité)")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        for row, tank in enumerate(TANK_ORDER):
            grid.addWidget(QLabel(TANK_LABELS[tank.value]), row, 0)

            slider = QSlider(Qt.Horizontal)
            slider.setRange(0, 1000)            # 0,000 à 1,000
            slider.setValue(int(self._sim.level(tank) * 1000))
            grid.addWidget(slider, row, 1)

            value = QLabel()
            value.setObjectName("value")
            value.setMinimumWidth(64)
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(value, row, 2)
            self._level_labels[tank] = value

            combo = _fault_combo()
            combo.setMinimumWidth(180)
            grid.addWidget(combo, row, 3)

            slider.valueChanged.connect(
                lambda raw, tank=tank: self._on_level(tank, raw)
            )
            combo.currentIndexChanged.connect(
                lambda index, tank=tank, combo=combo:
                self._sim.set_level_fault(tank, combo.itemData(index))
            )
            self._on_level(tank, slider.value())

        return box

    def _on_level(self, tank: TankId, raw: int) -> None:
        value = raw / 1000.0
        self._sim.set_level(tank, value)
        self._level_labels[tank].setText(fr(value, 3))

    # -- batterie -------------------------------------------------------
    def _build_battery(self) -> QGroupBox:
        box = QGroupBox("Batterie auxiliaire  (SmartShunt simulé)")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)
        battery = self._sim.battery()

        soc_slider = QSlider(Qt.Horizontal)
        soc_slider.setRange(0, 100)
        soc_slider.setValue(int(battery.soc_percent))
        soc_label = QLabel()
        soc_label.setObjectName("value")
        soc_label.setMinimumWidth(64)
        soc_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        grid.addWidget(QLabel("État de charge"), 0, 0)
        grid.addWidget(soc_slider, 0, 1)
        grid.addWidget(soc_label, 0, 2)

        def on_soc(value: int) -> None:
            self._sim.update_battery(soc_percent=float(value))
            soc_label.setText(f"{value} %")

        # (le pourcentage est entier : pas de virgule décimale à gérer ici)

        soc_slider.valueChanged.connect(on_soc)
        on_soc(soc_slider.value())

        voltage = _spin(0.0, 60.0, 2, 0.1, " V", battery.voltage_v)
        voltage.valueChanged.connect(
            lambda value: self._sim.update_battery(voltage_v=float(value))
        )
        grid.addWidget(QLabel("Tension"), 1, 0)
        grid.addWidget(voltage, 1, 1, 1, 2)

        current = _spin(-300.0, 300.0, 1, 0.5, " A", battery.current_a)
        current.valueChanged.connect(
            lambda value: self._sim.update_battery(current_a=float(value))
        )
        grid.addWidget(QLabel("Courant  (négatif = décharge)"), 2, 0)
        grid.addWidget(current, 2, 1, 1, 2)

        consumed = _spin(-1000.0, 0.0, 1, 1.0, " Ah", battery.consumed_ah)
        consumed.valueChanged.connect(
            lambda value: self._sim.update_battery(consumed_ah=float(value))
        )
        grid.addWidget(QLabel("Ah consommés"), 3, 0)
        grid.addWidget(consumed, 3, 1, 1, 2)

        ttg = _spin(0.0, 20000.0, 0, 30.0, " min", float(battery.time_to_go_min or 0))
        ttg.valueChanged.connect(
            lambda value: self._sim.update_battery(time_to_go_min=int(value))
        )
        grid.addWidget(QLabel("Autonomie annoncée"), 4, 0)
        grid.addWidget(ttg, 4, 1, 1, 2)

        provided = QCheckBox("le SmartShunt fournit l'autonomie")
        provided.setChecked(battery.time_to_go_available)
        provided.toggled.connect(
            lambda checked: self._sim.update_battery(time_to_go_available=bool(checked))
        )
        grid.addWidget(provided, 5, 1, 1, 2)

        combo = _fault_combo()
        grid.addWidget(QLabel("Liaison VE.Direct"), 6, 0)
        grid.addWidget(combo, 6, 1, 1, 2)
        combo.currentIndexChanged.connect(
            lambda index, combo=combo: self._sim.set_battery_fault(combo.itemData(index))
        )

        return box

    # -- chauffage ------------------------------------------------------
    def _build_heating(self) -> QGroupBox:
        box = QGroupBox("Circuits de chauffage  (matériel simulé)")
        grid = QGridLayout(box)
        grid.setColumnStretch(1, 1)

        row = 0
        for circuit in CIRCUIT_ORDER:
            valve = self._sim.valve(circuit)

            name = QLabel(CIRCUIT_LABELS[circuit.value])
            font = name.font()
            font.setBold(True)
            name.setFont(font)
            grid.addWidget(name, row, 0)

            buttons = QWidget()
            buttons_layout = QHBoxLayout(buttons)
            buttons_layout.setContentsMargins(0, 0, 0, 0)
            for label, action in (
                ("Ouvrir", ValveCommand.OPEN),
                ("Fermer", ValveCommand.CLOSE),
                ("Stop", ValveCommand.STOP),
            ):
                button = QPushButton(label)
                button.clicked.connect(
                    lambda _checked=False, circuit=circuit, action=action:
                    self._send_valve_command(circuit, action)
                )
                buttons_layout.addWidget(button)
            grid.addWidget(buttons, row, 1, 1, 2)
            row += 1

            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setValue(int(valve.position * 100))
            grid.addWidget(QLabel("Position réelle"), row, 0)
            grid.addWidget(bar, row, 1, 1, 2)
            self._valve_bars[circuit] = bar
            row += 1

            feedback = QCheckBox("retour de position disponible")
            feedback.setChecked(valve.feedback)
            feedback.toggled.connect(
                lambda checked, circuit=circuit:
                self._sim.set_valve_feedback(circuit, bool(checked))
            )
            fault = QCheckBox("actionneur en défaut")
            fault.toggled.connect(
                lambda checked, circuit=circuit:
                self._sim.set_valve_fault(circuit, bool(checked))
            )
            flags = QWidget()
            flags_layout = QHBoxLayout(flags)
            flags_layout.setContentsMargins(0, 0, 0, 0)
            flags_layout.addWidget(feedback)
            flags_layout.addWidget(fault)
            flags_layout.addStretch(1)
            grid.addWidget(flags, row, 1, 1, 2)
            row += 1

            travel = _spin(0.0, 60.0, 1, 0.5, " s", valve.travel_time_s)
            travel.valueChanged.connect(
                lambda value, circuit=circuit:
                self._sim.set_valve_travel_time(circuit, float(value))
            )
            grid.addWidget(QLabel("Temps de course"), row, 0)
            grid.addWidget(travel, row, 1, 1, 2)
            row += 1

        hint = QLabel(
            "Décocher « retour de position » reproduit le cas où l'actionneur "
            "ne dit rien de sa position :\nle logiciel doit alors écrire "
            "« commandé », jamais un état présenté comme certain."
        )
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        grid.addWidget(hint, row, 0, 1, 3)

        return box

    def _send_valve_command(self, circuit: CircuitId, action: ValveCommand) -> None:
        """Dépose un ordre dans la file — jamais d'appel direct au pilote."""
        self._app.command_bus.submit(
            ManualValveCommand(circuit=circuit, action=action)
        )

    # -- actions globales -----------------------------------------------
    def _build_actions(self) -> QGroupBox:
        box = QGroupBox("Actions")
        layout = QHBoxLayout(box)

        reset = QPushButton("Tout remettre en état normal")
        reset.clicked.connect(self._reset_faults)
        layout.addWidget(reset)
        layout.addStretch(1)
        return box

    def _reset_faults(self) -> None:
        self._sim.reset_faults()
        # Les listes déroulantes reflètent l'ancien état : on les remet à zéro.
        for combo in self.findChildren(QComboBox):
            combo.setCurrentIndex(0)
        for checkbox in self.findChildren(QCheckBox):
            if checkbox.text() == "actionneur en défaut":
                checkbox.setChecked(False)

    # ------------------------------------------------------------------
    # Rafraîchissement
    # ------------------------------------------------------------------
    def _start_refresh(self) -> None:
        hertz = float(self._app.config.get("general.ui_refresh_hz", 2)) or 2.0
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hertz))
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Relit l'instantané publié et redessine le volet de droite.

        Aucune entrée-sortie matérielle : uniquement de la lecture mémoire.
        """
        # Vue brute de l'acquisition : le panneau montre ce que les capteurs
        # renvoient, pas ce que l'écran du fourgon en fait.
        snapshot = self._app.acquisition.snapshot()

        scrollbar = self._readout.verticalScrollBar()
        position = scrollbar.value()
        self._readout.setPlainText(format_snapshot(snapshot))
        scrollbar.setValue(position)

        for circuit, bar in self._valve_bars.items():
            bar.setValue(int(self._sim.valve(circuit).position * 100))

    def closeEvent(self, event) -> None:        # noqa: N802 (API Qt)
        self._timer.stop()
        super().closeEvent(event)
