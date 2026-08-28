"""Bases de temps.

Toute la logique du programme utilise l'horloge **monotone** : le Raspberry
fonctionne sans Internet et sans horloge temps réel, son heure murale peut être
fausse ou sauter après un démarrage. L'heure murale ne sert qu'à l'affichage,
et elle est marquée comme non fiable tant qu'elle n'a pas été réglée.
"""

from __future__ import annotations

import time

#: Horloge de référence pour toute la logique (secondes, croissante, sans saut).
monotonic = time.monotonic


def wall_time() -> float:
    """Heure murale, uniquement pour l'affichage et l'horodatage indicatif."""
    return time.time()


#: Avant cette date, l'heure système est considérée comme jamais réglée.
#: (1er janvier 2024 — le logiciel a été écrit après.)
_PLAUSIBLE_EPOCH = 1_704_067_200.0


def wall_time_is_trustworthy() -> bool:
    """Vrai si l'heure murale a manifestement été réglée."""
    return wall_time() > _PLAUSIBLE_EPOCH


class Deadline:
    """Petite échéance sur l'horloge monotone.

    Sert à borner une suite d'opérations sans recalculer des soustractions
    partout ::

        deadline = Deadline(2.0)
        while not deadline.expired():
            ...
    """

    __slots__ = ("_end",)

    def __init__(self, seconds: float) -> None:
        self._end = monotonic() + max(0.0, seconds)

    def remaining(self) -> float:
        return max(0.0, self._end - monotonic())

    def expired(self) -> bool:
        return monotonic() >= self._end
