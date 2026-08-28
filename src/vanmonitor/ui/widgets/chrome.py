"""Bandeaux de l'application : haut, alertes, navigation.

Trois éléments présents sur les deux écrans, pour que le conducteur ne perde
jamais ses repères en passant de l'un à l'autre.
"""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QPainter, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from ...constants import AlertLevel
from ...models import Alert
from ...util.timebase import wall_time, wall_time_is_trustworthy
from .. import theme
from ..theme import Metrics
from .primitives import label, recolor


class TopBar(QFrame):
    """Titre, heure, et le seul témoin technique toléré : le mode simulation."""

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(metrics.topbar_height)
        self._metrics = metrics

        layout = QHBoxLayout(self)
        layout.setContentsMargins(metrics.px(12), 0, metrics.px(12), 0)
        layout.setSpacing(metrics.px(8))

        self._icon = _VanIcon(metrics)
        self._title = label("MONITORING VAN", size=metrics.font_small,
                            color=theme.TEXT, bold=True)
        font = self._title.font()
        font.setLetterSpacing(font.PercentageSpacing, 112)
        self._title.setFont(font)

        self._sim = label("SIM", size=metrics.font_tiny, color=theme.ORANGE, bold=True)
        self._sim.setStyleSheet(
            f"color: {theme.ORANGE}; background: transparent;"
            f" border: 1px solid {theme.ORANGE};"
            f" border-radius: {metrics.px(3)}px; padding: 1px {metrics.px(5)}px;"
        )
        self._sim.setVisible(False)

        self._clock = label("--:--", size=metrics.font_normal, color=theme.TEXT,
                            bold=True, align=Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self._icon)
        layout.addWidget(self._title)
        layout.addStretch(1)
        layout.addWidget(self._sim)
        layout.addWidget(self._clock)

    def refresh(self, *, simulation: bool) -> None:
        self._sim.setVisible(simulation)
        if wall_time_is_trustworthy():
            from time import localtime, strftime
            self._clock.setText(strftime("%H:%M", localtime(wall_time())))
        else:
            # Sans horloge temps réel ni Internet, l'heure peut être fausse :
            # mieux vaut ne rien afficher qu'une heure trompeuse.
            self._clock.setText("--:--")


class _VanIcon(QWidget):
    """Silhouette de fourgon, dessinée au trait."""

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self.setFixedSize(metrics.px(26), metrics.px(18))

    def paintEvent(self, event) -> None:        # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = self._metrics
        painter.setPen(QPen(QColor(theme.TEXT), max(1, metrics.px(2))))

        rect = QRectF(self.rect()).adjusted(1, 1, -1, -metrics.px(4))
        painter.drawRoundedRect(rect, metrics.px(3), metrics.px(3))
        painter.drawLine(int(rect.left() + rect.width() * 0.62), int(rect.top()),
                         int(rect.left() + rect.width() * 0.62), int(rect.bottom()))

        radius = metrics.px(2)
        for ratio in (0.28, 0.78):
            centre = rect.left() + rect.width() * ratio
            painter.drawEllipse(QRectF(centre - radius, rect.bottom() - radius,
                                       radius * 2, radius * 2))


class AlertBar(QFrame):
    """Zone d'alertes. Sans alerte, elle le dit — et c'est tout."""

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("topBar")
        self.setFixedHeight(metrics.alertbar_height)
        self._metrics = metrics

        layout = QHBoxLayout(self)
        layout.setContentsMargins(metrics.px(12), 0, metrics.px(12), 0)
        layout.setSpacing(metrics.px(8))

        self._marker = label("", size=metrics.font_normal, color=theme.TEXT_DIM, bold=True)
        self._text = label("Aucune alerte", size=metrics.font_small,
                           color=theme.TEXT_DIM)
        self._count = label("", size=metrics.font_small, color=theme.TEXT_MUTED,
                            align=Qt.AlignRight | Qt.AlignVCenter)

        layout.addWidget(self._marker)
        layout.addWidget(self._text, 1)
        layout.addWidget(self._count)

    def update_alerts(self, alerts: tuple[Alert, ...]) -> None:
        if not alerts:
            self.setStyleSheet("")
            self._marker.setText("")
            self._text.setText("Aucune alerte")
            recolor(self._text, theme.TEXT_DIM)
            self._count.setText("")
            return

        critical = any(alert.level is AlertLevel.CRITIQUE for alert in alerts)
        colour = theme.RED if critical else theme.AMBER
        # Fond teinté, sans clignotement ni animation.
        self.setStyleSheet(
            f"QFrame#topBar {{ background: {'#2A1618' if critical else '#2A2313'};"
            f" border: 1px solid {colour};"
            f" border-radius: {self._metrics.radius}px; }}"
        )
        self._marker.setText("⚠")
        recolor(self._marker, colour)

        # Toutes les alertes actives sont nommées, séparées par des points.
        self._text.setText("   ·   ".join(alert.message for alert in alerts))
        recolor(self._text, colour)
        self._count.setText(f"({len(alerts)})" if len(alerts) > 1 else "")
        recolor(self._count, colour)


class NavBar(QFrame):
    """Navigation : Accueil, Paramètres. Rien d'autre à atteindre."""

    navigate = pyqtSignal(str)

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("navBar")
        self.setFixedHeight(metrics.navbar_height)
        self._metrics = metrics
        self._buttons: dict[str, QPushButton] = {}

        layout = QHBoxLayout(self)
        layout.setContentsMargins(metrics.px(6), metrics.px(5),
                                  metrics.px(6), metrics.px(5))
        layout.setSpacing(metrics.px(6))

        layout.addStretch(1)
        for key, text in (("home", "Accueil"), ("settings", "Paramètres")):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setProperty("nav", "true")
            button.setFixedHeight(metrics.px(36))
            button.setFixedWidth(metrics.px(190))
            button.clicked.connect(lambda _checked, key=key: self.navigate.emit(key))
            layout.addWidget(button)
            self._buttons[key] = button
        layout.addStretch(1)

        self.set_current("home")

    def set_current(self, key: str) -> None:
        for name, button in self._buttons.items():
            selected = name == key
            button.setChecked(selected)
            button.setProperty("selected", "true" if selected else "false")
            button.style().unpolish(button)
            button.style().polish(button)
