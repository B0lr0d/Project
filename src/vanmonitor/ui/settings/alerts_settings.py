"""Section ALERTES : quatre seuils, et l'interrupteur des alertes techniques."""

from __future__ import annotations

from PyQt5.QtWidgets import QPushButton, QWidget

from ...config import ConfigStore
from ...models import SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.dialogs import NumericKeypad, SegmentedControl
from ..widgets.primitives import Card, label
from . import SettingsSection, field_row, value_button

#: Les quatre seuils, dans l'ordre où ils comptent pour le conducteur.
THRESHOLDS = [
    ("alerts.battery_soc_min_pct", "Batterie basse", "en dessous de"),
    ("alerts.fresh_water_min_pct", "Eau propre basse", "en dessous de"),
    ("alerts.fuel_min_pct", "Gasoil bas", "en dessous de"),
    ("alerts.grey_water_max_pct", "Eaux grises hautes", "au-dessus de"),
]


class AlertsSettings(SettingsSection):
    def __init__(self, config: ConfigStore, metrics: Metrics,
                 parent: QWidget | None = None) -> None:
        super().__init__(metrics, parent)
        self._config = config
        self._buttons: dict[str, QPushButton] = {}

        card = Card("Seuils d'alerte", metrics)
        for path, title, direction in THRESHOLDS:
            button = value_button("", metrics)
            button.clicked.connect(lambda _checked, path=path, title=title:
                                   self._edit(path, title))
            self._buttons[path] = button
            card.body().addLayout(
                field_row(f"{title}  ({direction})", button, metrics)
            )
        self.column().addWidget(card)

        technical = Card("Alertes techniques", metrics)
        technical.body().addWidget(label(
            "Sonde critique absente, SmartShunt injoignable, défaut d'un circuit.",
            size=metrics.font_small, color=theme.TEXT_MUTED,
        ))
        self._technical = SegmentedControl(
            [("on", "Activées"), ("off", "Désactivées")], metrics
        )
        self._technical.on_change(self._set_technical)
        technical.body().addWidget(self._technical)
        self.column().addWidget(technical)

        reset = QPushButton("Rétablir les valeurs par défaut")
        reset.setMinimumHeight(metrics.touch_min)
        reset.clicked.connect(self._reset)
        self.column().addWidget(reset)
        self.column().addStretch(1)

        self._reload()

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        for path, _title, _direction in THRESHOLDS:
            self._buttons[path].setText(f"{float(self._config.get(path, 0)):.0f} %")
        self._technical.set_current(
            "on" if self._config.get("alerts.technical_alerts", True) else "off"
        )

    def _edit(self, path: str, title: str) -> None:
        dialog = NumericKeypad(
            title, float(self._config.get(path, 0)), self._metrics,
            unit="%", decimals=0, minimum=0, maximum=100, parent=self,
        )
        if dialog.exec_() and dialog.value is not None:
            self._config.set(path, round(dialog.value))
            self._reload()

    def _set_technical(self, key: str) -> bool:
        self._config.set("alerts.technical_alerts", key == "on")
        return True

    def _reset(self) -> None:
        self._config.reset_section("alerts")
        self._reload()

    def refresh(self, snapshot: SystemSnapshot) -> None:
        pass
