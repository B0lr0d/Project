"""Page Paramètres : un rail de sections à gauche, le contenu à droite.

Deux niveaux, pas un de plus. Le rail reste visible en permanence : on sait
toujours où l'on est, et revenir en arrière ne demande jamais de chercher.
"""

from __future__ import annotations

from typing import Callable

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QScroller,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import ConfigStore
from ..core.commands import CommandBus
from ..core.services import HeatingService, TankService
from ..models import SystemSnapshot
from .layout_profile import LayoutProfile
from .settings.alerts_settings import AlertsSettings
from .settings.calibration_settings import CalibrationSettings
from .settings.display_settings import DisplaySettings
from .settings.heating_settings import HeatingSettings
from .settings.history_settings import HistorySettings
from .settings.sensors_settings import SensorsSettings
from .theme import Metrics

SECTIONS = [
    ("heating", "Chauffage"),
    ("alerts", "Alertes"),
    ("calibration", "Calibration"),
    ("sensors", "Sondes"),
    ("display", "Écran"),
    ("history", "Historique"),
]


class SettingsPage(QWidget):
    def __init__(
        self,
        config: ConfigStore,
        heating: HeatingService,
        tanks: TankService,
        command_bus: CommandBus,
        sensor_ids: list[str],
        metrics: Metrics,
        profile: LayoutProfile,
        implicit_bindings: dict | None = None,
        on_identification: Callable[[bool], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._metrics = metrics

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(metrics.gap)

        # --- rail des sections ------------------------------------------
        rail = QWidget()
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(0, 0, 0, 0)
        rail_layout.setSpacing(metrics.px(4))
        rail.setFixedWidth(metrics.px(132 if profile is LayoutProfile.STANDARD else 104))

        self._buttons: dict[str, QPushButton] = {}
        for key, text in SECTIONS:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("nav", "true")
            button.setFixedHeight(metrics.nav_touch)
            button.clicked.connect(lambda _checked, key=key: self.show_section(key))
            rail_layout.addWidget(button)
            self._buttons[key] = button
        rail_layout.addStretch(1)
        layout.addWidget(rail)

        # --- contenu ----------------------------------------------------
        self._sections = {
            "heating": HeatingSettings(heating, command_bus, metrics),
            "alerts": AlertsSettings(config, metrics),
            "calibration": CalibrationSettings(tanks, metrics),
            "sensors": SensorsSettings(config, sensor_ids, metrics,
                                       implicit_bindings=implicit_bindings,
                                       on_identification=on_identification),
            "display": DisplaySettings(config, metrics),
            "history": HistorySettings(config, metrics),
        }

        self._stack = QStackedWidget()
        self._scrolls: dict[str, QScrollArea] = {}
        for key, _text in SECTIONS:
            scroll = QScrollArea()
            scroll.setWidget(self._sections[key])
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            # Jamais de défilement horizontal : tout doit tenir en largeur.
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            # Les commandes étant dimensionnées pour le doigt, une section ne
            # tient plus toujours d'un seul écran. La barre reste donc visible
            # en permanence : mieux vaut savoir qu'il y a une suite que de
            # croire la page tronquée.
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            # Et l'on fait défiler en tirant la page, pas en visant la barre.
            QScroller.grabGesture(scroll.viewport(),
                                  QScroller.LeftMouseButtonGesture)
            self._scrolls[key] = scroll
            self._stack.addWidget(scroll)
        layout.addWidget(self._stack, 1)

        self.show_section("heating")

    # ------------------------------------------------------------------
    def show_section(self, key: str) -> None:
        for name, button in self._buttons.items():
            selected = name == key
            button.setChecked(selected)
            button.setProperty("selected", "true" if selected else "false")
            button.style().unpolish(button)
            button.style().polish(button)
        self._stack.setCurrentWidget(self._scrolls[key])

    def update_snapshot(self, snapshot: SystemSnapshot) -> None:
        """Seule la section visible est rafraîchie : inutile de peindre le reste."""
        for key, scroll in self._scrolls.items():
            if scroll is self._stack.currentWidget():
                self._sections[key].refresh(snapshot)
