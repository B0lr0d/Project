"""Actionneurs des clapets de chauffage.

MATERIEL À INTEGRER PLUS TARD (H-3) : type d'actionneur, alimentation, et
surtout **présence ou non d'un retour de position** ne sont pas déterminés.

Le point à ne pas perdre de vue au moment de l'intégration : si l'actionneur
retenu ne fournit pas de position réelle, ``get_confirmed_state()`` doit
renvoyer ``INCONNU`` — et rien d'autre. Y renvoyer une valeur déduite de
l'ordre transmis ferait afficher à l'écran un état physique présenté comme
certain alors qu'il ne l'est pas.
"""

from __future__ import annotations

from ...constants import ConfirmedState, ValveCommand, ValveState
from ..interfaces import ValveDriver


class RealValveDriver(ValveDriver):
    """MATERIEL À INTEGRER PLUS TARD — implémentation prévue à l'étape 11."""

    def __init__(self, **params: object) -> None:
        raise NotImplementedError(
            "MATERIEL À INTEGRER PLUS TARD : actionneurs de clapets non choisis (H-3). "
            "Utiliser le mode simulation (--sim) en attendant."
        )

    def open(self) -> None:                             # pragma: no cover - souche
        raise NotImplementedError

    def close(self) -> None:                            # pragma: no cover - souche
        raise NotImplementedError

    def stop(self) -> None:                             # pragma: no cover - souche
        raise NotImplementedError

    def get_commanded_state(self) -> ValveCommand:      # pragma: no cover - souche
        raise NotImplementedError

    def has_position_feedback(self) -> bool:            # pragma: no cover - souche
        raise NotImplementedError

    def get_confirmed_state(self) -> ConfirmedState:    # pragma: no cover - souche
        raise NotImplementedError

    def get_state(self) -> ValveState:                  # pragma: no cover - souche
        raise NotImplementedError

    def has_fault(self) -> bool:                        # pragma: no cover - souche
        raise NotImplementedError
