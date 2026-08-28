"""Briques de base : carte, titre, jauges, indicateurs.

Tout ce qui est dessiné à la main l'est au ``QPainter``, sans image ni police
d'icônes : rien à installer sur le Raspberry, et un rendu net à n'importe
quelle échelle.
"""

from __future__ import annotations

from PyQt5.QtCore import QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

from .. import theme
from ..theme import Metrics


def french(value: float, decimals: int = 1, *, signed: bool = False) -> str:
    """Nombre à la française : virgule décimale."""
    return f"{value:{'+' if signed else ''}.{decimals}f}".replace(".", ",")


class Card(QFrame):
    """Carte : fond légèrement plus clair, bordure discrète, coins arrondis."""

    def __init__(self, title: str | None, metrics: Metrics,
                 parent: QWidget | None = None, *, inner: bool = False) -> None:
        super().__init__(parent)
        self.setObjectName("innerCard" if inner else "card")
        self._metrics = metrics

        self._layout = QVBoxLayout(self)
        pad = metrics.px(7)
        self._layout.setContentsMargins(pad, pad, pad, pad)
        self._layout.setSpacing(metrics.px(4))

        self.title_label: QLabel | None = None
        if title:
            self.title_label = QLabel(title.upper())
            self.title_label.setObjectName("cardTitle")
            self._layout.addWidget(self.title_label)

    def body(self) -> QVBoxLayout:
        return self._layout


def label(text: str, *, size: int, color: str = theme.TEXT,
          bold: bool = False, align=Qt.AlignLeft | Qt.AlignVCenter) -> QLabel:
    """Étiquette réglée d'un coup.

    La taille passe par la feuille de style de l'étiquette, et non par
    ``setFont`` : la règle ``QWidget { font-size }`` de la feuille globale est
    plus spécifique qu'une police posée par code, et l'écraserait sans bruit.
    """
    widget = QLabel(text)
    widget.setProperty("_size", size)
    widget.setProperty("_bold", bold)
    widget.setAlignment(align)
    recolor(widget, color)
    return widget


def recolor(widget: QLabel, color: str) -> None:
    """Change la couleur d'une étiquette sans perdre sa taille ni sa graisse."""
    size = widget.property("_size") or 14
    bold = bool(widget.property("_bold"))
    widget.setStyleSheet(
        f"color: {color}; background: transparent;"
        f" font-size: {size}px; font-weight: {700 if bold else 400};"
    )


class ValueLabel(QWidget):
    """Grande valeur suivie de son unité en plus petit, alignées sur la base."""

    def __init__(self, metrics: Metrics, color: str = theme.TEXT,
                 parent: QWidget | None = None, *, size: int | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(metrics.px(2))

        main = size or metrics.font_huge
        self._value = label("--", size=main, color=color, bold=True)
        self._unit = label("", size=max(metrics.font_small, round(main * 0.55)),
                           color=theme.TEXT_MUTED,
                           bold=True, align=Qt.AlignLeft | Qt.AlignBottom)
        layout.addWidget(self._value, 0, Qt.AlignBottom)
        layout.addWidget(self._unit, 0, Qt.AlignBottom)
        layout.addStretch(1)

    def set_value(self, text: str, unit: str = "", color: str | None = None) -> None:
        self._value.setText(text)
        self._unit.setText(unit)
        if color is not None:
            recolor(self._value, color)


class BarGauge(QWidget):
    """Jauge horizontale : fond sombre, remplissage coloré, texte centré.

    C'est la forme retenue sur la maquette : un bloc plein qui se lit d'un coup
    d'œil, sans graduation ni décoration.
    """

    def __init__(self, metrics: Metrics, color: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._color = QColor(color)
        self._ratio: float | None = None
        self._text = "--"
        self._alert = False
        self.setFixedHeight(metrics.px(30))
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def set_state(self, ratio: float | None, text: str, *, alert: bool = False) -> None:
        self._ratio = None if ratio is None else max(0.0, min(1.0, ratio))
        self._text = text
        self._alert = alert
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = self._metrics.px(5)

        colour = QColor(theme.RED) if self._alert else self._color
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)

        # Fond : teinte sourde de la couleur du réservoir, pour que le texte
        # reste lisible qu'il tombe sur le remplissage ou à côté.
        painter.fillPath(path, QColor(theme.tint(colour.name(), 0.22)))

        if self._ratio is not None and self._ratio > 0:
            painter.save()
            painter.setClipPath(path)
            fill = QRectF(rect)
            fill.setWidth(rect.width() * self._ratio)
            painter.fillRect(fill, colour)
            painter.restore()

        painter.setPen(QPen(QColor(theme.BORDER), 1))
        painter.drawPath(path)

        font = painter.font()
        font.setPixelSize(self._metrics.font_small)
        font.setBold(True)
        painter.setFont(font)

        # Le texte se place dans la partie remplie quand il y tient, sinon
        # juste après : jamais à cheval sur la limite, où il devient illisible.
        pad = self._metrics.px(6)
        text_width = painter.fontMetrics().width(self._text)
        filled = rect.width() * (self._ratio or 0.0)

        if filled >= text_width + 2 * pad:
            area = QRectF(rect.left(), rect.top(), filled, rect.height())
            painter.setPen(QColor(theme.BACKGROUND))
        else:
            area = QRectF(rect.left() + filled, rect.top(),
                          rect.width() - filled, rect.height())
            painter.setPen(QColor(theme.TEXT if self._ratio else theme.TEXT_MUTED))
        painter.drawText(area, Qt.AlignCenter, self._text)


class BatteryIcon(QWidget):
    """Pictogramme de batterie, rempli proportionnellement à la charge."""

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._ratio: float | None = None
        self._color = QColor(theme.GREEN)
        self.setFixedSize(metrics.px(74), metrics.px(34))

    def set_state(self, ratio: float | None, color: str) -> None:
        self._ratio = None if ratio is None else max(0.0, min(1.0, ratio))
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = self._metrics

        cap = metrics.px(3)
        body = QRectF(self.rect()).adjusted(1, 1, -cap - 2, -1)
        radius = metrics.px(4)

        painter.setPen(QPen(QColor(theme.BORDER_STRONG), max(1, metrics.px(2))))
        painter.setBrush(QColor(theme.BACKGROUND))
        painter.drawRoundedRect(body, radius, radius)

        terminal = QRectF(
            body.right() + metrics.px(1), body.center().y() - metrics.px(5),
            cap + metrics.px(1), metrics.px(10),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(theme.BORDER_STRONG))
        painter.drawRoundedRect(terminal, metrics.px(2), metrics.px(2))

        if self._ratio:
            inner = body.adjusted(metrics.px(3), metrics.px(3),
                                  -metrics.px(3), -metrics.px(3))
            inner.setWidth(inner.width() * self._ratio)
            painter.setBrush(self._color)
            painter.drawRoundedRect(inner, metrics.px(2), metrics.px(2))


class ValveIndicator(QWidget):
    """Symbole d'électrovanne : corps rond sur une conduite, plus une commande.

    La distinction confirmé / commandé est portée par la **forme** autant que
    par la couleur : corps plein pour un état confirmé par le matériel, corps
    évidé pour un état seulement commandé. Un œil qui distingue mal les teintes
    doit pouvoir faire la différence.
    """

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._color = QColor(theme.TEXT_DIM)
        self._filled = False
        self._crossed = False
        self.setFixedSize(metrics.px(44), metrics.px(30))

    def set_state(self, color: str, *, filled: bool, crossed: bool = False) -> None:
        self._color = QColor(color)
        self._filled = filled
        self._crossed = crossed
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = self._metrics
        width = max(2, metrics.px(3))
        rect = QRectF(self.rect())

        diameter = min(rect.height() - width, rect.width() * 0.52)
        body = QRectF(0, 0, diameter, diameter)
        body.moveCenter(rect.center())
        body.moveTop(rect.bottom() - diameter - width / 2)

        # Conduite de part et d'autre du corps de vanne.
        painter.setPen(QPen(self._color, width, Qt.SolidLine, Qt.RoundCap))
        centre_y = body.center().y()
        painter.drawLine(int(rect.left() + width), int(centre_y),
                         int(body.left()), int(centre_y))
        painter.drawLine(int(body.right()), int(centre_y),
                         int(rect.right() - width), int(centre_y))
        # Tige de commande.
        painter.drawLine(int(body.center().x()), int(rect.top() + width),
                         int(body.center().x()), int(body.top()))
        painter.drawLine(int(body.center().x() - diameter * 0.34), int(rect.top() + width),
                         int(body.center().x() + diameter * 0.34), int(rect.top() + width))

        painter.setBrush(self._color if self._filled else QColor(theme.CARD_INNER))
        painter.drawEllipse(body)

        if self._crossed:
            painter.setPen(QPen(QColor(theme.BACKGROUND if self._filled else theme.RED),
                                width))
            inset = body.width() * 0.28
            painter.drawLine(
                int(body.left() + inset), int(body.top() + inset),
                int(body.right() - inset), int(body.bottom() - inset),
            )
            painter.drawLine(
                int(body.right() - inset), int(body.top() + inset),
                int(body.left() + inset), int(body.bottom() - inset),
            )


class ThermometerIcon(QWidget):
    """Petit thermomètre, marqueur des lignes de température."""

    def __init__(self, metrics: Metrics, color: str = theme.BLUE,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._color = QColor(color)
        self.setFixedSize(metrics.px(9), metrics.px(16))

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect())
        width = max(1.5, rect.width() * 0.34)

        painter.setPen(QPen(self._color, width, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(int(rect.center().x()), int(rect.top() + width),
                         int(rect.center().x()), int(rect.bottom() - width * 1.6))

        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        bulb = rect.width() * 0.82
        painter.drawEllipse(QRectF(rect.center().x() - bulb / 2,
                                   rect.bottom() - bulb, bulb, bulb))


class Dot(QWidget):
    """Petite pastille de couleur, pour les listes."""

    def __init__(self, metrics: Metrics, color: str = theme.TEXT_MUTED,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._color = QColor(color)
        self._metrics = metrics
        self.setFixedSize(metrics.px(8), metrics.px(8))

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event) -> None:        # noqa: N802 (API Qt)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())
