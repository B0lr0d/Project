"""Section SONDES : associer chaque identifiant DS18B20 à une zone.

Une sonde 1-Wire n'a pas d'étiquette, elle a un numéro de série. Tout l'enjeu
de cette page est de faire le lien entre ce numéro et un endroit du fourgon.

Deux moyens, et ils se complètent :

* **la liste se met à jour toute seule** — le bus est balayé à chaque cycle de
  lecture, une sonde débranchée disparaît, une sonde branchée apparaît ;
* **le bouton Identifier** — on réchauffe une sonde à la main et on regarde
  quelle ligne monte. Tant que cette page est ouverte, les sondes détectées
  mais associées à aucune zone sont lues elles aussi, pour qu'on puisse les
  reconnaître **avant** de décider où elles vont.

Cette lecture supplémentaire s'arrête dès qu'on quitte la page : sur un bus
1-Wire, chaque sonde lue en plus occupe le bus près d'une seconde.
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QComboBox, QGridLayout, QPushButton, QVBoxLayout, QWidget

from ...config import ConfigStore
from ...constants import Status, ZONE_ORDER, ZoneId
from ...models import Sample, SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.primitives import Card, french, label, recolor
from . import SettingsSection

UNASSIGNED = "— non associée —"


def _missing_label(sensor_id: str) -> str:
    return f"{sensor_id}  (absente)"


class SensorsSettings(SettingsSection):
    def __init__(self, config: ConfigStore, available_ids: list[str],
                 metrics: Metrics, parent: QWidget | None = None,
                 implicit_bindings: dict[ZoneId, str] | None = None,
                 on_identification: Callable[[bool], None] | None = None) -> None:
        super().__init__(metrics, parent)
        self._config = config
        # En simulation, une zone sans association est reliée d'office à la
        # sonde simulée correspondante. L'écran doit le montrer, sinon il
        # annoncerait « non associée » pendant que la température s'affiche.
        self._implicit = implicit_bindings or {}
        self._on_identification = on_identification
        self._identifying: ZoneId | None = None
        self._known_ids: tuple[str, ...] = ()
        self._combos: dict[ZoneId, QComboBox] = {}
        self._values: dict[ZoneId, object] = {}

        card = Card("Sondes de température (DS18B20)", metrics)

        self._detected = label("Balayage du bus…", size=metrics.font_small,
                               color=theme.TEXT_MUTED)
        card.body().addWidget(self._detected)

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

        # --- sondes détectées mais associées à aucune zone ----------------
        self._loose_card = Card("Sondes détectées, non associées", metrics)
        self._loose_layout = QVBoxLayout()
        self._loose_layout.setSpacing(metrics.px(3))
        self._loose_card.body().addLayout(self._loose_layout)
        self._loose_card.setVisible(False)
        self.column().addWidget(self._loose_card)

        self._note = label("", size=metrics.font_small, color=theme.TEXT_MUTED)
        self._note.setWordWrap(True)
        self.column().addWidget(self._note)
        self.column().addStretch(1)

        self._implicit_shown = False
        self._rebuild_choices(tuple(available_ids))
        self._show_default_note()

    # ------------------------------------------------------------------
    # Cycle de vie : la lecture des sondes libres suit l'ouverture de la page
    # ------------------------------------------------------------------
    def showEvent(self, event) -> None:         # noqa: N802 (API Qt)
        super().showEvent(event)
        if self._on_identification is not None:
            self._on_identification(True)

    def hideEvent(self, event) -> None:         # noqa: N802 (API Qt)
        super().hideEvent(event)
        if self._on_identification is not None:
            self._on_identification(False)

    # ------------------------------------------------------------------
    def _bound_id(self, zone: ZoneId) -> str | None:
        configured = self._config.get(f"temperatures.zones.{zone.value}.sensor_id")
        if configured:
            return str(configured)
        return self._implicit.get(zone)

    def _rebuild_choices(self, detected: tuple[str, ...]) -> None:
        """Reconstruit les listes déroulantes quand le bus a changé.

        Une sonde associée mais absente du bus reste proposée, marquée comme
        telle : l'association ne doit pas disparaître silencieusement parce
        qu'un câble s'est débranché.
        """
        bound = {self._bound_id(zone) for zone in ZONE_ORDER}
        missing = sorted(
            sensor_id for sensor_id in bound
            if sensor_id and sensor_id not in detected
        )

        self._known_ids = detected
        implicit = False
        for zone, combo in self._combos.items():
            current = self._bound_id(zone)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(UNASSIGNED, None)
            for sensor_id in detected:
                combo.addItem(sensor_id, sensor_id)
            for sensor_id in missing:
                combo.addItem(_missing_label(sensor_id), sensor_id)
            index = combo.findData(current) if current else 0
            combo.setCurrentIndex(max(0, index))
            combo.blockSignals(False)

            if current and not self._config.get(
                f"temperatures.zones.{zone.value}.sensor_id"
            ):
                implicit = True

        self._implicit_shown = implicit
        count = len(detected)
        text = f"Sondes détectées sur le bus : {count}"
        if missing:
            text += f"   ·   {len(missing)} associée(s) mais absente(s)"
        self._detected.setText(text)
        recolor(self._detected, theme.AMBER if missing else theme.TEXT_MUTED)

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
        # L'association implicite de simulation ne vaut plus pour cette zone.
        self._implicit.pop(zone, None)
        self._rebuild_choices(self._known_ids)

    def _identify(self, zone: ZoneId) -> None:
        self._identifying = None if self._identifying is zone else zone
        for other, value in self._values.items():
            recolor(value, theme.ORANGE if other is self._identifying else theme.TEXT)
        self._show_default_note()

    def _show_default_note(self) -> None:
        if self._identifying is not None:
            target = self._combos[self._identifying].currentText()
            self._note.setText(
                f"Suivi de {target} : réchauffez cette sonde à la main et "
                "regardez sa valeur monter."
            )
            return

        text = ("Identifier : réchauffez une sonde à la main, sa température "
                "monte à l'écran — c'est elle. Le bus est rebalayé à chaque "
                "cycle de lecture.")
        if self._implicit_shown:
            text = ("Simulation : les zones non réglées sont associées "
                    "automatiquement aux sondes simulées.   ·   ") + text
        self._note.setText(text)

    # ------------------------------------------------------------------
    def refresh(self, snapshot: SystemSnapshot) -> None:
        if snapshot.available_sensor_ids != self._known_ids:
            self._rebuild_choices(snapshot.available_sensor_ids)

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

        self._refresh_loose_sensors(snapshot)

    def _refresh_loose_sensors(self, snapshot: SystemSnapshot) -> None:
        """Affiche les sondes détectées qui n'appartiennent encore à personne."""
        bound = {self._bound_id(zone) for zone in ZONE_ORDER}
        loose = [
            sensor_id for sensor_id in snapshot.available_sensor_ids
            if sensor_id not in bound
        ]

        while self._loose_layout.count():
            item = self._loose_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._loose_card.setVisible(bool(loose))
        if not loose:
            return

        metrics = self._metrics
        for sensor_id in loose:
            sample: Sample | None = snapshot.sensor_temperatures.get(sensor_id)
            if sample is not None and sample.ok and sample.value is not None:
                text = f"{sensor_id}      {french(float(sample.value))} °C"
            elif sample is not None and sample.status is Status.FAULT:
                text = f"{sensor_id}      Erreur"
            else:
                text = f"{sensor_id}      --"
            self._loose_layout.addWidget(
                label(text, size=metrics.font_small, color=theme.TEXT_MUTED)
            )
