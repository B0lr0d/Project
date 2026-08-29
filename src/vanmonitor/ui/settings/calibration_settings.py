"""Section CALIBRATION : la mesure brute, les points relevés, l'aperçu.

La calibration se fait réservoir en cours de remplissage : on ajoute un point à
chaque palier connu. L'ordre de saisie n'a aucune importance, la table est
triée toute seule.

Une table incohérente n'est jamais enregistrée : le message explique quoi
corriger, et la table précédente reste active entre-temps.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QPushButton, QWidget

from ...constants import TANK_ORDER, TankId
from ...core.calibration import CalibrationError, CalibrationPoint, CalibrationTable
from ...core.services import TankService
from ...models import SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.dialogs import NumericKeypad, SegmentedControl
from ..widgets.primitives import Card, french, label
from . import SettingsSection, field_row


class CalibrationSettings(SettingsSection):
    def __init__(self, tanks: TankService, metrics: Metrics,
                 parent: QWidget | None = None) -> None:
        super().__init__(metrics, parent)
        self._tanks = tanks
        self._current = TankId.EAU_PROPRE
        self._raw: float | None = None
        self._draft: CalibrationTable | None = None

        selector = Card("Réservoir à calibrer", metrics)
        self._selector = SegmentedControl(
            [(tank.value, tanks.label(tank)) for tank in TANK_ORDER], metrics
        )
        self._selector.on_change(self._select)
        self._selector.set_current(self._current.value)
        selector.body().addWidget(self._selector)
        self.column().addWidget(selector)

        measure = Card("Mesure brute actuelle", metrics)
        self._raw_label = label("--", size=metrics.font_big, color=theme.TEXT, bold=True)
        measure.body().addLayout(field_row("Valeur lue par le capteur",
                                           self._raw_label, metrics))
        self._preview = label("", size=metrics.font_small, color=theme.TEXT_MUTED)
        self._preview.setWordWrap(True)
        measure.body().addWidget(self._preview)
        self.column().addWidget(measure)

        self._points_card = Card("Points enregistrés", metrics)
        self._points_grid = QGridLayout()
        self._points_grid.setHorizontalSpacing(metrics.px(10))
        self._points_grid.setVerticalSpacing(metrics.px(3))
        self._points_card.body().addLayout(self._points_grid)

        self._empty = label("Aucun point : le réservoir n'est pas encore calibré.",
                            size=metrics.font_small, color=theme.TEXT_DIM)
        self._points_card.body().addWidget(self._empty)
        self.column().addWidget(self._points_card)

        actions = QHBoxLayout()
        actions.setSpacing(metrics.px(6))
        add = QPushButton("+  Ajouter le point actuel")
        add.setMinimumHeight(metrics.touch_min)
        add.setProperty("accent", "true")
        add.clicked.connect(self._add_point)
        clear = QPushButton("Effacer la table")
        clear.setMinimumHeight(metrics.touch_min)
        clear.setProperty("danger", "true")
        clear.clicked.connect(self._clear)
        actions.addWidget(add, 2)
        actions.addWidget(clear, 1)
        self.column().addLayout(actions)

        self._message = label("", size=metrics.font_small, color=theme.RED)
        self._message.setWordWrap(True)
        self.column().addWidget(self._message)
        self.column().addStretch(1)

        self._reload()

    # ------------------------------------------------------------------
    @property
    def _table(self) -> CalibrationTable:
        """Table sur laquelle porte l'édition : celle réellement enregistrée."""
        if self._draft is not None:
            return self._draft
        return self._tanks.table(self._current)

    @property
    def _shown_table(self) -> CalibrationTable:
        """Table effectivement utilisée par l'affichage.

        En simulation, un réservoir non calibré est converti par une table de
        démonstration. L'écran doit le dire, sinon l'accueil afficherait des
        litres pendant que cette page annonce un réservoir non calibré.
        """
        table = self._table
        if table.is_valid():
            return table
        return self._tanks.effective_table(self._current)

    @property
    def _is_demo(self) -> bool:
        return not self._table.is_valid() and self._shown_table.is_valid()

    def _unit_label(self) -> str:
        return "L" if self._tanks.shows_litres(self._current) else "%"

    def _select(self, key: str) -> bool:
        self._current = TankId(key)
        self._draft = None
        self._message.setText("")
        self._reload()
        return True

    def _reload(self) -> None:
        table = self._shown_table
        demo = self._is_demo
        self._points_card.title_label.setText(
            "POINTS DE DÉMONSTRATION (SIMULATION)" if demo else "POINTS ENREGISTRÉS"
        )
        while self._points_grid.count():
            item = self._points_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        metrics = self._metrics
        self._empty.setText(
            "Table de démonstration : le réservoir n'est pas calibré. "
            "Le premier point ajouté démarre une vraie table."
            if demo else
            "Aucun point : le réservoir n'est pas encore calibré."
        )
        self._empty.setVisible(demo or not table.points)
        if table.points:
            headers = ("BRUT", self._unit_label().upper(), "")
            for column, text in enumerate(headers):
                self._points_grid.addWidget(
                    label(text, size=metrics.font_tiny, color=theme.TEXT_MUTED, bold=True),
                    0, column,
                )
            for row, point in enumerate(table.points, start=1):
                self._points_grid.addWidget(
                    label(french(point.raw, 3), size=metrics.font_small,
                          color=theme.TEXT), row, 0)
                self._points_grid.addWidget(
                    label(french(point.value, 0), size=metrics.font_small,
                          color=theme.TEXT, align=Qt.AlignRight | Qt.AlignVCenter),
                    row, 1)
                delete = QPushButton("Suppr.")
                delete.setProperty("compact", "true")
                delete.setEnabled(not demo)     # rien à supprimer dans une démo
                delete.clicked.connect(
                    lambda _checked, raw=point.raw: self._remove(raw)
                )
                self._points_grid.addWidget(delete, row, 2)

        self._update_preview()

    def _update_preview(self) -> None:
        table = self._shown_table
        capacity = table.effective_capacity()

        parts = []
        if capacity is not None and self._unit_label() == "L":
            declared = self._tanks.table(self._current).capacity_l
            parts.append(
                f"Capacité : {french(capacity, 0)} L "
                f"({'déclarée' if declared is not None else 'déduite du dernier point'})"
            )

        if self._raw is not None and table.is_valid():
            try:
                litres, out_of_range = table.litres(self._raw)
                percent, _ = table.percent(self._raw)
            except CalibrationError:
                litres, percent, out_of_range = None, None, False
            preview = f"Aperçu : brut {french(self._raw, 3)} → "
            preview += f"{french(litres, 1)} L → " if litres is not None else ""
            preview += f"{french(percent, 0)} %" if percent is not None else "--"
            if out_of_range:
                preview += "   (hors plage)"
            parts.append(preview)
        elif not table.is_valid():
            parts.append("Au moins deux points sont nécessaires pour convertir.")

        self._preview.setText("\n".join(parts))

    # ------------------------------------------------------------------
    def _add_point(self) -> None:
        if self._raw is None:
            self._message.setText("Aucune mesure disponible : capteur absent ou en panne.")
            return

        unit = self._unit_label()
        dialog = NumericKeypad(
            f"{self._tanks.label(self._current)} — contenu réel actuel",
            None, self._metrics, unit=unit, decimals=0,
            minimum=0, maximum=100 if unit == "%" else 2000, parent=self,
        )
        if not dialog.exec_() or dialog.value is None:
            return

        candidate = self._table.with_point(self._raw, dialog.value)
        self._commit(candidate)

    def _remove(self, raw: float) -> None:
        self._commit(self._table.without_point(raw), allow_incomplete=True)

    def _clear(self) -> None:
        self._commit(self._table.cleared(), allow_incomplete=True)

    def _commit(self, table: CalibrationTable, *, allow_incomplete: bool = False) -> None:
        """Enregistre si la table est valide ; sinon la garde en brouillon.

        Supprimer l'avant-dernier point rendrait la table invalide : on ne la
        rejette pas pour autant, on la conserve à l'écran pour que l'utilisateur
        puisse finir sa saisie. Ce qui est enregistré, lui, reste cohérent.
        """
        try:
            self._tanks.save_table(self._current, table)
        except CalibrationError as exc:
            if allow_incomplete or len(table) < 2:
                self._draft = table
                self._message.setText(str(exc))
                self._reload()
                return
            self._message.setText(str(exc))
            return

        self._draft = None
        self._message.setText("")
        self._reload()

    # ------------------------------------------------------------------
    def refresh(self, snapshot: SystemSnapshot) -> None:
        reading = snapshot.tanks.get(self._current)
        self._raw = reading.raw if reading else None
        self._raw_label.setText("--" if self._raw is None else french(self._raw, 3))
        self._update_preview()
