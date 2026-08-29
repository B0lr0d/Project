"""Les sections de la page Paramètres.

Rien de technique n'y figure : périodes de scrutation, délais, filtres et
chemins de fichiers restent dans le fichier de configuration. Le conducteur y
trouve ce qu'il peut avoir besoin de changer en route, et rien d'autre.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ...models import SystemSnapshot
from .. import theme
from ..theme import Metrics
from ..widgets.primitives import Card, label


class SettingsSection(QWidget):
    """Base commune : une colonne de cartes, rafraîchie par l'instantané."""

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._column = QVBoxLayout(self)
        self._column.setContentsMargins(0, 0, 0, 0)
        self._column.setSpacing(metrics.gap)

    def column(self) -> QVBoxLayout:
        return self._column

    def refresh(self, snapshot: SystemSnapshot) -> None:
        """Redessine à partir de l'instantané. Par défaut, rien à faire."""


def value_button(text: str, metrics: Metrics) -> QPushButton:
    """Champ de saisie tactile : un bouton qui ouvre le pavé numérique."""
    button = QPushButton(text)
    button.setMinimumHeight(metrics.touch_min)
    button.setMinimumWidth(metrics.px(92))
    return button


def field_row(text: str, widget: QWidget, metrics: Metrics,
              *, stretch: int = 0) -> QHBoxLayout:
    """Une ligne « libellé … contrôle », alignée et respirante."""
    row = QHBoxLayout()
    row.setSpacing(metrics.px(8))
    row.addWidget(label(text, size=metrics.font_small, color=theme.TEXT_MUTED), 1)
    row.addWidget(widget, stretch, Qt.AlignRight)
    return row


__all__ = ["SettingsSection", "Card", "field_row", "value_button"]
