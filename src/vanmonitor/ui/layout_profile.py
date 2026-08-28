"""Profil de disposition choisi à l'exécution.

Le modèle d'écran n'est pas arrêté : la mise en page doit tenir de 480 × 272
à 1024 × 600 sans qu'une seule position soit écrite en pixels absolus.

Deux profils seulement, parce qu'au-delà on ne saurait plus les tester :

* **standard** — les quatre cartes du haut sur une ligne, comme la maquette ;
* **compact** — les mêmes cartes sur deux rangées de deux, pour les dalles
  étroites. Rien ne disparaît, tout se réorganise.
"""

from __future__ import annotations

from enum import Enum

#: En dessous de cette largeur, quatre cartes sur une ligne deviennent illisibles.
COMPACT_WIDTH_THRESHOLD = 720


class LayoutProfile(Enum):
    STANDARD = "standard"
    COMPACT = "compact"

    @property
    def tanks_per_row(self) -> int:
        return 4 if self is LayoutProfile.STANDARD else 2

    @property
    def shows_secondary_metrics(self) -> bool:
        """En compact, la carte batterie renonce aux Ah et à l'autonomie."""
        return self is LayoutProfile.STANDARD

    @property
    def shows_thresholds(self) -> bool:
        """En compact, les seuils de chauffage restent dans les Paramètres."""
        return self is LayoutProfile.STANDARD

    @property
    def shows_gauges(self) -> bool:
        """En compact, la valeur chiffrée passe avant la jauge, faute de place."""
        return self is LayoutProfile.STANDARD


def profile_for(width: int, height: int) -> LayoutProfile:
    if width < COMPACT_WIDTH_THRESHOLD:
        return LayoutProfile.COMPACT
    return LayoutProfile.STANDARD
