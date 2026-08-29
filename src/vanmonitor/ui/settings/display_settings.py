"""Section ÉCRAN : veille de l'affichage.

Un seul réglage, et une phrase pour dire ce que la veille ne fait pas — parce
que c'est la question que tout le monde se pose devant un écran qui s'éteint
dans un fourgon : est-ce que ça continue de surveiller ?
"""

from __future__ import annotations

from PyQt5.QtWidgets import QWidget

from ...config import ConfigStore
from ...models import SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.dialogs import SegmentedControl
from ..widgets.primitives import Card, label, recolor
from . import SettingsSection, field_row

#: Choix proposés. La clé est le délai en secondes ; ``0`` désactive la veille.
DELAY_CHOICES = [
    ("0", "Désactivée"),
    ("60", "1 min"),
    ("300", "5 min"),
    ("600", "10 min"),
    ("1800", "30 min"),
]


class DisplaySettings(SettingsSection):
    def __init__(self, config: ConfigStore, metrics: Metrics,
                 parent: QWidget | None = None) -> None:
        super().__init__(metrics, parent)
        self._config = config

        card = Card("Veille de l'écran", metrics)

        self._delay = SegmentedControl(DELAY_CHOICES, metrics)
        self._delay.on_change(self._set_delay)
        card.body().addWidget(self._delay)

        self._state = label("--", size=metrics.font_small, color=theme.TEXT_MUTED)
        card.body().addLayout(field_row("État", self._state, metrics))
        self.column().addWidget(card)

        note = label(
            "L'écran seul s'éteint. Le Raspberry reste allumé : les "
            "températures, les niveaux et la batterie continuent d'être lus, "
            "le chauffage continue de réguler, et les alertes restent actives.",
            size=metrics.font_small, color=theme.TEXT_MUTED,
        )
        note.setWordWrap(True)
        self.column().addWidget(note)

        self._wake_note = label(
            "Le premier appui sur un écran éteint sert uniquement à le "
            "rallumer : il ne déclenche aucune commande.",
            size=metrics.font_small, color=theme.TEXT_MUTED,
        )
        self._wake_note.setWordWrap(True)
        self.column().addWidget(self._wake_note)

        self._warning = label("", size=metrics.font_small, color=theme.AMBER)
        self._warning.setWordWrap(True)
        self.column().addWidget(self._warning)
        self.column().addStretch(1)

        self._reload()

    # ------------------------------------------------------------------
    def _reload(self) -> None:
        enabled = bool(self._config.get("display.sleep_enabled", True))
        delay = int(float(self._config.get("display.sleep_delay_s", 300)))
        key = "0" if not enabled else str(delay)
        if key not in {choice for choice, _label in DELAY_CHOICES}:
            key = "300"
        self._delay.set_current(key)

    def _set_delay(self, key: str) -> bool:
        seconds = int(key)
        if seconds == 0:
            self._config.set("display.sleep_enabled", False)
        else:
            self._config.update({
                "display.sleep_enabled": True,
                "display.sleep_delay_s": seconds,
            })
        return True

    # ------------------------------------------------------------------
    def refresh(self, snapshot: SystemSnapshot) -> None:
        display = snapshot.display
        if display is None:
            return

        if not display.available:
            self._state.setText("indisponible sur ce système")
            recolor(self._state, theme.TEXT_DIM)
            self._warning.setText(
                f"Aucune méthode d'extinction n'a été trouvée ({display.method}). "
                "Le réglage est conservé mais l'écran ne s'éteindra pas."
            )
            return

        self._warning.setText(display.last_error or "")
        if display.asleep:
            self._state.setText("écran en veille")
            recolor(self._state, theme.ORANGE)
            return

        if not display.enabled:
            self._state.setText("écran allumé   ·   veille désactivée")
        else:
            remaining = max(0, int(display.delay_s - display.idle_s))
            minutes, seconds = divmod(remaining, 60)
            self._state.setText(
                f"écran allumé   ·   veille dans {minutes:d} min {seconds:02d}"
            )
        recolor(self._state, theme.TEXT_MUTED)
