"""Palette, typographie et dimensions de l'interface.

Un seul endroit décide de l'apparence. Deux conséquences utiles : changer une
couleur ne demande pas de fouiller le code, et les tailles s'adaptent à la
dalle réelle sans qu'aucun widget ne connaisse sa résolution.

Toutes les dimensions sont exprimées pour une hauteur de référence de 480 px
puis mises à l'échelle. Aucune position absolue n'est écrite ailleurs.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..constants import TankId

# ---------------------------------------------------------------------------
# Couleurs
# ---------------------------------------------------------------------------

#: Fond général, très sombre : de nuit, un fond clair éblouit le conducteur.
BACKGROUND = "#0A0D11"
#: Fond des cartes, légèrement plus clair que le fond général.
CARD = "#141920"
#: Fond des éléments imbriqués (sous-cartes chauffage, champs).
CARD_INNER = "#1B212A"
#: Bordures discrètes : elles séparent, elles ne décorent pas.
BORDER = "#252C36"
BORDER_STRONG = "#333C48"

TEXT = "#F2F5F9"
TEXT_MUTED = "#8A96A6"
TEXT_DIM = "#5C6875"

#: Couleurs fonctionnelles. Chacune porte un sens, aucune n'est décorative.
GREEN = "#3ED860"          # batterie, état sain
BLUE = "#3B9DFF"           # eau propre
GREY_WATER = "#95A3B4"     # eaux grises
AMBER = "#F5A623"          # gasoil
ORANGE = "#F0883E"         # chauffage ouvert, état commandé non confirmé
RED = "#F04747"            # alerte, défaut

#: Couleur d'accent des réservoirs.
#:
#: Réglé d'après la capture de référence : bleu pour l'eau propre, gris pour
#: les eaux grises, ambre pour le gasoil. Le texte de la demande mentionnait
#: orange pour les eaux grises et vert pour le gasoil — les deux se
#: permutent ici, en une ligne.
TANK_COLORS: dict[TankId, str] = {
    TankId.EAU_PROPRE: BLUE,
    TankId.EAUX_GRISES: GREY_WATER,
    TankId.GASOIL: AMBER,
}

#: Familles disponibles à la fois sur Raspberry Pi OS et sur un PC ordinaire.
FONT_FAMILY = '"DejaVu Sans", "Noto Sans", "Segoe UI", sans-serif'


def tint(color: str, ratio: float, over: str = BACKGROUND) -> str:
    """Mélange une couleur vers le fond : sert aux fonds de jauge.

    Une jauge dont le fond est une teinte sourde de sa propre couleur se lit
    d'un coup d'œil, même quand le remplissage est très bas.
    """
    def channels(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))

    front, back = channels(color), channels(over)
    mixed = tuple(
        round(back[index] + (front[index] - back[index]) * ratio) for index in range(3)
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

#: Hauteur de référence des maquettes.
BASE_HEIGHT = 480

#: Cible tactile minimale, en millimètres puis convertie selon la dalle.
MIN_TOUCH_MM = 9.0


@dataclass(frozen=True)
class Metrics:
    """Toutes les tailles de l'interface, mises à l'échelle de la dalle."""

    scale: float

    def px(self, value: float) -> int:
        """Met une dimension de référence à l'échelle, au pixel entier."""
        return max(1, round(value * self.scale))

    # -- typographie ----------------------------------------------------
    @property
    def font_huge(self) -> int:         # valeur principale d'une carte
        return self.px(38)

    @property
    def font_big(self) -> int:          # valeur secondaire marquée
        return self.px(21)

    @property
    def font_normal(self) -> int:
        return self.px(14)

    @property
    def font_small(self) -> int:
        return self.px(12)

    @property
    def font_tiny(self) -> int:         # titres de cartes, mentions
        return self.px(10)

    # -- espacements ----------------------------------------------------
    @property
    def gap(self) -> int:
        return self.px(7)

    @property
    def margin(self) -> int:
        return self.px(8)

    @property
    def radius(self) -> int:
        return self.px(9)

    @property
    def radius_small(self) -> int:
        return self.px(6)

    # -- bandeaux -------------------------------------------------------
    @property
    def topbar_height(self) -> int:
        return self.px(38)

    @property
    def alertbar_height(self) -> int:
        return self.px(34)

    @property
    def navbar_height(self) -> int:
        return self.px(52)

    @property
    def touch_min(self) -> int:
        return self.px(38)


def metrics_for(width: int, height: int) -> Metrics:
    """Échelle déduite de la dalle réelle, bornée pour rester lisible."""
    scale = min(height / BASE_HEIGHT, width / 800.0)
    return Metrics(scale=max(0.62, min(1.9, scale)))


# ---------------------------------------------------------------------------
# Feuille de style
# ---------------------------------------------------------------------------

def stylesheet(metrics: Metrics) -> str:
    """Feuille de style Qt complète, dérivée de la palette et de l'échelle."""
    return f"""
QWidget {{
    background: {BACKGROUND};
    color: {TEXT};
    font-family: {FONT_FAMILY};
    font-size: {metrics.font_normal}px;
}}

QFrame#card {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {metrics.radius}px;
}}
QFrame#innerCard {{
    background: {CARD_INNER};
    border: 1px solid {BORDER};
    border-radius: {metrics.radius_small}px;
}}
QFrame#topBar, QFrame#navBar {{
    background: {CARD};
    border: 1px solid {BORDER};
    border-radius: {metrics.radius}px;
}}

QLabel#cardTitle {{
    color: {TEXT_MUTED};
    font-size: {metrics.font_tiny}px;
    font-weight: 700;
    letter-spacing: 1px;
}}
QLabel#muted {{ color: {TEXT_MUTED}; font-size: {metrics.font_small}px; }}
QLabel#dim   {{ color: {TEXT_DIM};   font-size: {metrics.font_small}px; }}

QPushButton {{
    background: {CARD_INNER};
    color: {TEXT};
    border: 1px solid {BORDER_STRONG};
    border-radius: {metrics.radius_small}px;
    padding: {metrics.px(4)}px {metrics.px(10)}px;
    min-height: {metrics.px(34)}px;
    font-size: {metrics.font_small}px;
    font-weight: 600;
}}
QPushButton:pressed {{ background: {BORDER_STRONG}; }}
QPushButton:disabled {{ color: {TEXT_DIM}; border-color: {BORDER}; }}
QPushButton[accent="true"] {{
    background: {BLUE}; color: {BACKGROUND}; border-color: {BLUE};
}}
QPushButton[danger="true"] {{
    background: transparent; color: {RED}; border-color: {RED};
}}
QPushButton[selected="true"] {{
    background: {BLUE}; color: {BACKGROUND}; border-color: {BLUE};
}}
QPushButton[flat="true"] {{
    background: transparent; border: none; min-height: 0px;
}}
QPushButton[nav="true"] {{
    padding: 0px; min-height: {metrics.px(30)}px; font-size: {metrics.font_normal}px;
}}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{
    background: transparent; width: {metrics.px(9)}px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {BORDER_STRONG}; border-radius: {metrics.px(4)}px;
    min-height: {metrics.px(30)}px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QComboBox {{
    background: {CARD_INNER}; border: 1px solid {BORDER_STRONG};
    border-radius: {metrics.radius_small}px;
    padding: {metrics.px(6)}px {metrics.px(10)}px;
    min-height: {metrics.touch_min}px;
}}
QComboBox::drop-down {{ border: none; width: {metrics.px(22)}px; }}
QComboBox QAbstractItemView {{
    background: {CARD_INNER}; border: 1px solid {BORDER_STRONG};
    selection-background-color: {BLUE}; selection-color: {BACKGROUND};
    outline: none;
}}

QDialog {{ background: {BACKGROUND}; }}
"""
