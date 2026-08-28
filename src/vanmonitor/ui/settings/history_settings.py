"""Section HISTORIQUE : activer, espacer, purger.

Désactivé par défaut, et désactivable à tout moment sans rien casser : chaque
écriture use la carte microSD, et un fourgon n'a pas besoin de statistiques
pour rouler.

Étape 9 : l'enregistrement lui-même. Les réglages sont ici, et ils sont déjà
lus par la configuration.
"""

from __future__ import annotations

from pathlib import Path

from PyQt5.QtWidgets import QPushButton, QWidget

from ...config import ConfigStore
from ...models import SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.dialogs import SegmentedControl
from ..widgets.primitives import Card, label
from . import SettingsSection, field_row

PERIODS = [("60", "1 min"), ("300", "5 min"), ("900", "15 min"), ("1800", "30 min")]
RETENTIONS = [("6", "6 h"), ("12", "12 h"), ("24", "24 h"), ("48", "48 h")]


class HistorySettings(SettingsSection):
    def __init__(self, config: ConfigStore, metrics: Metrics,
                 parent: QWidget | None = None) -> None:
        super().__init__(metrics, parent)
        self._config = config

        card = Card("Historique", metrics)

        self._enabled = SegmentedControl([("on", "Activé"), ("off", "Désactivé")], metrics)
        self._enabled.on_change(self._set_enabled)
        card.body().addLayout(field_row("Enregistrement", self._enabled, metrics, stretch=2))

        self._period = SegmentedControl(PERIODS, metrics)
        self._period.on_change(self._set_period)
        card.body().addLayout(
            field_row("Fréquence d'enregistrement", self._period, metrics, stretch=3)
        )

        self._retention = SegmentedControl(RETENTIONS, metrics)
        self._retention.on_change(self._set_retention)
        card.body().addLayout(
            field_row("Durée de conservation", self._retention, metrics, stretch=3)
        )

        self._size = label("--", size=metrics.font_small, color=theme.TEXT)
        card.body().addLayout(field_row("Taille actuelle", self._size, metrics))

        self.column().addWidget(card)

        clear = QPushButton("Effacer l'historique")
        clear.setMinimumHeight(metrics.touch_min)
        clear.setProperty("danger", "true")
        clear.clicked.connect(self._clear)
        self.column().addWidget(clear)

        self._note = label(
            "Désactivé, l'historique n'ouvre aucun fichier et n'écrit rien : "
            "c'est le réglage qui préserve le plus la carte microSD.",
            size=metrics.font_small, color=theme.TEXT_MUTED,
        )
        self._note.setWordWrap(True)
        self.column().addWidget(self._note)
        self.column().addStretch(1)

        self._reload()

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        enabled = bool(self._config.get("history.enabled", False))
        self._enabled.set_current("on" if enabled else "off")
        self._period.set_current(
            str(int(self._config.get("history.sample_period_s", 300)))
        )
        self._retention.set_current(
            str(int(self._config.get("history.retention_hours", 24)))
        )
        self._period.setEnabled(enabled)
        self._retention.setEnabled(enabled)
        self._size.setText(self._database_size())

    def _database_size(self) -> str:
        if not self._config.get("history.enabled", False):
            return "base absente (historique désactivé)"
        path = Path(str(self._config.get("history.db_path", "")))
        try:
            size = path.stat().st_size
        except OSError:
            return "base pas encore créée"
        return f"{size / 1024:.0f} ko"

    def _set_enabled(self, key: str) -> bool:
        self._config.set("history.enabled", key == "on")
        self._reload()
        return True

    def _set_period(self, key: str) -> bool:
        self._config.set("history.sample_period_s", int(key))
        return True

    def _set_retention(self, key: str) -> bool:
        self._config.set("history.retention_hours", int(key))
        return True

    def _clear(self) -> None:
        path = Path(str(self._config.get("history.db_path", "")))
        try:
            path.unlink()
        except OSError:
            pass
        self._reload()

    def refresh(self, snapshot: SystemSnapshot) -> None:
        pass
