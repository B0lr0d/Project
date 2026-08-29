"""Filtres appliqués aux mesures brutes.

Un fourgon secoue ses capteurs, son bus 1-Wire longe des câbles d'alimentation
et ses réservoirs ballottent. Une mesure isolée aberrante n'est donc pas une
anomalie rare : c'est le quotidien.

Le parti pris est de **ne jamais lisser au point de mentir**. Une valeur
isolée invraisemblable est écartée ; si la suivante la confirme, c'est que le
changement est réel et il est accepté sans retard supplémentaire. Une porte
qu'on ouvre en hiver fait vraiment chuter la température de la cabine, et
l'écran doit le montrer.

Étape 5 : filtre médian et moyenne exponentielle pour les niveaux, dont la
nature du bruit est différente (ballottement continu plutôt que trames
corrompues).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SpikeGuard:
    """Écarte une valeur isolée trop éloignée de la précédente.

    Le principe est celui du témoin : une valeur qui s'écarte de plus de
    ``max_step`` est mise en attente plutôt que publiée. Si la mesure suivante
    la confirme — c'est-à-dire si elle est proche de la valeur suspecte — le
    changement est réel et les deux sont acceptées. Sinon, la première est
    oubliée : c'était une trame corrompue.

    Coût du filtre : un seul cycle de retard, et uniquement sur un changement
    brutal. Une évolution normale n'est jamais retardée.
    """

    max_step: float
    _last: float | None = None
    _pending: float | None = None
    _rejected: int = 0

    def reset(self) -> None:
        """Oublie l'historique : après réassociation ou remise en service."""
        self._last = None
        self._pending = None
        self._rejected = 0

    @property
    def rejected(self) -> int:
        """Nombre de valeurs écartées depuis la dernière remise à zéro."""
        return self._rejected

    def accept(self, value: float) -> tuple[bool, str | None]:
        """Retourne ``(accepté, raison du rejet)``."""
        if self.max_step <= 0 or self._last is None:
            self._last = value
            self._pending = None
            return True, None

        if abs(value - self._last) <= self.max_step:
            self._last = value
            self._pending = None
            return True, None

        if self._pending is not None and abs(value - self._pending) <= self.max_step:
            # Deux mesures concordantes : le changement est réel.
            self._last = value
            self._pending = None
            return True, None

        previous, self._pending = self._last, value
        self._rejected += 1
        return False, (
            f"écart isolé de {abs(value - previous):.1f} au-delà de "
            f"{self.max_step:g} — en attente de confirmation"
        )
