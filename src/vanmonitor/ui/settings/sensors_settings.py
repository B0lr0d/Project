"""Section SONDES : associer chaque identifiant DS18B20 à une zone.

Une sonde 1-Wire n'a pas d'étiquette : elle a un numéro de série. Le bouton
« Identifier » sert à faire le lien physique — on réchauffe une sonde à la
main et on regarde laquelle bouge.

Étape 4 : détection du bus réel et rafraîchissement de la liste. Ici, la liste
provient de la configuration et, en simulation, des sondes simulées.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QGridLayout, QPushButton, QWidget

from ...config import ConfigStore
from ...constants import Status, ZONE_ORDER, ZoneId
from ...models import SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.primitives import Card, french, label
from . import SettingsSection

UNASSIGNED = "— non associée —"


class SensorsSettings(SettingsSection):
    def __init__(self, config: ConfigStore, available_ids: list[str],
                 metrics: Metrics, parent: QWidget | None = None,
                 implicit_bindings: dict[ZoneId, str] | None = None) -> None:
        super().__init__(metrics, parent)
        self._config = config
        self._available = available_ids
        # En simulation, une zone sans association est reliée d'office à la
        # sonde simulée correspondante. L'écran doit le montrer, sinon il
        # annoncerait « non associée » pendant que la température s'affiche.
        self._implicit = implicit_bindings or {}
        self._combos: dict[ZoneId, QComboBox] = {}
        self._values: dict[ZoneId, object] = {}
        self._identifying: ZoneId | None = None

        card = Card("Sondes de température (DS18B20)", metrics)
        grid = QGridLayout()
        grid.setHorizontalSpacing(metrics.px(8))
        grid.setVerticalSpacing(metrics.px(6))
        grid.setColumnStretch(1, 1)

        for row, zone in enumerate(ZONE_ORDER):
            zone_label = str(config.get(f"temperatures.zones.{zone.value}.label",
                                        zone.value))
            grid.addWidget(
                label(zone_label, size=metrics.font_small, color=theme.TEXT), row, 0
            )

            combo = QComboBox()
            combo.addItem(UNASSIGNED, None)
            for sensor_id in available_ids:
                combo.addItem(sensor_id, sensor_id)
            combo.setMinimumHeight(metrics.touch_min)
            combo.currentIndexChanged.connect(
                lambda _index, zone=zone: self._bind(zone)
            )
            grid.addWidget(combo, row, 1)
            self._combos[zone] = combo

            value = label("--", size=metrics.font_small, color=theme.TEXT, bold=True,
                          align=Qt.AlignRight | Qt.AlignVCenter)
            value.setMinimumWidth(metrics.px(70))
            grid.addWidget(value, row, 2)
            self._values[zone] = value

            identify = QPushButton("Identifier")
            identify.setMinimumHeight(metrics.touch_min)
            identify.clicked.connect(lambda _checked, zone=zone: self._identify(zone))
            grid.addWidget(identify, row, 3)

        card.body().addLayout(grid)
        self.column().addWidget(card)

        self._note = label("", size=metrics.font_small, color=theme.TEXT_MUTED)
        self._note.setWordWrap(True)
        self.column().addWidget(self._note)
        self.column().addStretch(1)

        self._implicit_shown = False
        self._reload()
        self._show_default_note()

    def _show_default_note(self) -> None:
        text = ("Identifier : réchauffez une sonde à la main, sa température "
                "monte à l'écran — c'est elle.")
        if self._implicit_shown:
            text = ("Simulation : les zones non réglées sont associées "
                    "automatiquement aux sondes simulées.   ·   ") + text
        self._note.setText(text)

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        implicit = False
        for zone, combo in self._combos.items():
            sensor_id = self._config.get(f"temperatures.zones.{zone.value}.sensor_id")
            if not sensor_id and zone in self._implicit:
                sensor_id = self._implicit[zone]
                implicit = True
            index = combo.findData(sensor_id) if sensor_id else 0
            combo.blockSignals(True)
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)
        self._implicit_shown = implicit

    def _bind(self, zone: ZoneId) -> None:
        sensor_id = self._combos[zone].currentData()
        updates = {f"temperatures.zones.{zone.value}.sensor_id": sensor_id}

        # Une même sonde ne peut pas servir deux zones : l'ancienne est libérée.
        if sensor_id:
            for other in ZONE_ORDER:
                if other is zone:
                    continue
                path = f"temperatures.zones.{other.value}.sensor_id"
                if self._config.get(path) == sensor_id:
                    updates[path] = None
        self._config.update(updates)
        self._reload()

    def _identify(self, zone: ZoneId) -> None:
        self._identifying = None if self._identifying is zone else zone
        for other, value in self._values.items():
            value.setStyleSheet(
                f"color: {theme.ORANGE if other is self._identifying else theme.TEXT};"
                " background: transparent;"
            )
        if self._identifying is None:
            self._show_default_note()
            return
        target = self._combos[zone].currentText()
        self._note.setText(
            f"Suivi de {target} : réchauffez-la et regardez la valeur monter."
        )

    # ------------------------------------------------------------------
    def refresh(self, snapshot: SystemSnapshot) -> None:
        for zone, widget in self._values.items():
            reading = snapshot.temperatures.get(zone)
            if reading is None:
                continue
            if reading.status is Status.OK and reading.celsius is not None:
                widget.setText(f"{french(reading.celsius)} °C")
            elif reading.status is Status.FAULT:
                widget.setText("Erreur")
            else:
                widget.setText("--")
