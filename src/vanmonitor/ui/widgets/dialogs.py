"""Boîtes de dialogue tactiles : pavé numérique et confirmation de sécurité.

Aucune saisie ne passe par le clavier du système : sur un écran de 4,3 pouces
dans un fourgon, un clavier virtuel générique est inutilisable. Un pavé
numérique plein écran, avec de grosses touches, l'est.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import theme
from ..theme import Metrics
from .primitives import label


class SegmentedControl(QWidget):
    """Choix exclusif entre deux ou trois options, en gros boutons collés."""

    def __init__(self, options: list[tuple[str, str]], metrics: Metrics,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._buttons: dict[str, QPushButton] = {}
        self._current: str | None = None
        self._callback = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(metrics.px(4))

        for key, text in options:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setMinimumHeight(metrics.touch_min)
            button.clicked.connect(lambda _checked, key=key: self._choose(key))
            layout.addWidget(button, 1)
            self._buttons[key] = button

    def on_change(self, callback) -> None:
        """La fonction reçoit la clé choisie et retourne True si elle l'accepte.

        Ce retour permet à un réglage de refuser un choix (mode AUTO sans
        seuils) ou de le soumettre à confirmation (repli sur perte de sonde)
        sans que le bouton mente entre-temps.
        """
        self._callback = callback

    def set_current(self, key: str) -> None:
        self._current = key
        for name, button in self._buttons.items():
            selected = name == key
            button.setChecked(selected)
            button.setProperty("selected", "true" if selected else "false")
            button.style().unpolish(button)
            button.style().polish(button)

    def set_enabled_keys(self, keys: set[str]) -> None:
        for name, button in self._buttons.items():
            button.setEnabled(name in keys)

    def _choose(self, key: str) -> None:
        previous = self._current
        if self._callback is not None and not self._callback(key):
            self.set_current(previous or key)   # refusé : rien ne bouge
            return
        self.set_current(key)


class NumericKeypad(QDialog):
    """Saisie d'une valeur numérique au doigt."""

    def __init__(self, title: str, value: float | None, metrics: Metrics,
                 *, unit: str = "", decimals: int = 1,
                 minimum: float = -999.0, maximum: float = 9999.0,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self._metrics = metrics
        self._decimals = decimals
        self._minimum = minimum
        self._maximum = maximum
        self._text = "" if value is None else f"{value:.{decimals}f}".rstrip("0").rstrip(".")
        self.value: float | None = None

        layout = QVBoxLayout(self)
        pad = metrics.px(10)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(metrics.px(8))

        layout.addWidget(label(title, size=metrics.font_small, color=theme.TEXT_MUTED))

        self._display = label("", size=metrics.font_huge, color=theme.TEXT, bold=True,
                              align=Qt.AlignRight | Qt.AlignVCenter)
        self._display.setStyleSheet(
            f"color: {theme.TEXT}; background: {theme.CARD};"
            f" border: 1px solid {theme.BORDER};"
            f" border-radius: {metrics.radius}px; padding: {metrics.px(6)}px;"
        )
        layout.addWidget(self._display)
        self._unit = unit

        grid = QGridLayout()
        grid.setSpacing(metrics.px(5))
        keys = [
            ("7", 0, 0), ("8", 0, 1), ("9", 0, 2),
            ("4", 1, 0), ("5", 1, 1), ("6", 1, 2),
            ("1", 2, 0), ("2", 2, 1), ("3", 2, 2),
            (",", 3, 0), ("0", 3, 1), ("←", 3, 2),
        ]
        for text, row, column in keys:
            button = QPushButton(text)
            button.setMinimumHeight(metrics.px(48))
            button.clicked.connect(lambda _checked, text=text: self._press(text))
            grid.addWidget(button, row, column)

        minus = QPushButton("−/+")
        minus.setMinimumHeight(metrics.px(48))
        minus.clicked.connect(lambda: self._press("-"))
        grid.addWidget(minus, 3, 3)

        for row, step in enumerate((10.0, 1.0, 0.5)):
            plus = QPushButton(f"+{step:g}".replace(".", ","))
            plus.setMinimumHeight(metrics.px(48))
            plus.clicked.connect(lambda _checked, step=step: self._nudge(step))
            grid.addWidget(plus, row, 3)

        layout.addLayout(grid)

        actions = QHBoxLayout()
        actions.setSpacing(metrics.px(6))
        cancel = QPushButton("Annuler")
        cancel.setMinimumHeight(metrics.px(46))
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("Valider")
        confirm.setMinimumHeight(metrics.px(46))
        confirm.setProperty("accent", "true")
        confirm.clicked.connect(self._accept)
        actions.addWidget(cancel, 1)
        actions.addWidget(confirm, 1)
        layout.addLayout(actions)

        self._refresh()

    # ------------------------------------------------------------------
    def _press(self, key: str) -> None:
        if key == "←":
            self._text = self._text[:-1]
        elif key == "-":
            self._text = self._text[1:] if self._text.startswith("-") else "-" + self._text
        elif key == ",":
            if "," not in self._text and self._decimals > 0:
                self._text = (self._text or "0") + ","
        else:
            self._text += key
        self._refresh()

    def _nudge(self, step: float) -> None:
        current = self._parse() or 0.0
        self._text = f"{current + step:.{self._decimals}f}".replace(".", ",")
        self._refresh()

    def _parse(self) -> float | None:
        try:
            return float(self._text.replace(",", "."))
        except ValueError:
            return None

    def _refresh(self) -> None:
        text = self._text or "0"
        self._display.setText(f"{text} {self._unit}".strip())

    def _accept(self) -> None:
        value = self._parse()
        if value is None:
            self.reject()
            return
        self.value = max(self._minimum, min(self._maximum, value))
        self.accept()


class ConfirmDialog(QDialog):
    """Confirmation d'un réglage de sécurité.

    ``ANNULER`` est le choix mis en avant, et fermer la fenêtre sans répondre
    équivaut à annuler : un réglage qui engage la protection contre le gel ne
    doit jamais changer par inadvertance.
    """

    def __init__(self, heading: str, subject: str, transition: str, consequence: str,
                 metrics: Metrics, *, severe: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(heading)

        layout = QVBoxLayout(self)
        pad = metrics.px(14)
        layout.setContentsMargins(pad, pad, pad, pad)
        layout.setSpacing(metrics.px(8))

        header = label(f"⚠  {heading.upper()}", size=metrics.font_small,
                       color=theme.AMBER, bold=True)
        layout.addWidget(header)
        layout.addWidget(label(subject, size=metrics.font_normal, color=theme.TEXT))

        transition_label = label(transition, size=metrics.font_big,
                                 color=theme.RED if severe else theme.ORANGE, bold=True)
        layout.addWidget(transition_label)

        body = label(consequence, size=metrics.font_small, color=theme.TEXT_MUTED)
        body.setWordWrap(True)
        body.setMinimumWidth(metrics.px(330))
        layout.addWidget(body)
        layout.addSpacing(metrics.px(4))

        actions = QHBoxLayout()
        actions.setSpacing(metrics.px(8))
        cancel = QPushButton("Annuler")
        cancel.setMinimumHeight(metrics.px(46))
        cancel.setProperty("accent", "true")     # le choix par défaut
        cancel.clicked.connect(self.reject)
        confirm = QPushButton("Confirmer")
        confirm.setMinimumHeight(metrics.px(46))
        if severe:
            confirm.setProperty("danger", "true")
        confirm.clicked.connect(self.accept)
        actions.addWidget(cancel, 1)
        actions.addWidget(confirm, 1)
        layout.addLayout(actions)

        cancel.setDefault(True)
        cancel.setFocus()
