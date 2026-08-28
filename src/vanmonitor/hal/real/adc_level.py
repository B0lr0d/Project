"""Capteurs de niveau via convertisseur analogique-numérique.

MATERIEL À INTEGRER PLUS TARD (H-1 et H-2) :

* technologie des capteurs de niveau non choisie ;
* modèle du convertisseur, bus et nombre de voies non choisis.

Rien n'est supposé ici — ni tension, ni brochage, ni plage de mesure. Le seul
engagement déjà pris est le contrat : ``read_raw()`` rend une valeur **brute**
sans unité, et la calibration multipoints lui donnera un sens.
"""

from __future__ import annotations

from ..interfaces import ADCInterface, LevelSensor


class ADCLevelSensor(LevelSensor):
    """MATERIEL À INTEGRER PLUS TARD — implémentation prévue à l'étape 11."""

    def __init__(self, adc: ADCInterface, channel: str, *, timeout_s: float = 1.0) -> None:
        raise NotImplementedError(
            "MATERIEL À INTEGRER PLUS TARD : capteurs de niveau et convertisseur "
            "analogique-numérique non choisis (H-1, H-2). "
            "Utiliser le mode simulation (--sim) en attendant."
        )

    def read_raw(self) -> float:                # pragma: no cover - souche
        raise NotImplementedError

    def is_present(self) -> bool:               # pragma: no cover - souche
        raise NotImplementedError


class RealADC(ADCInterface):
    """MATERIEL À INTEGRER PLUS TARD — dépend du convertisseur retenu."""

    def __init__(self, **params: object) -> None:
        raise NotImplementedError(
            "MATERIEL À INTEGRER PLUS TARD : modèle de convertisseur non choisi (H-2)."
        )

    def read_channel(self, channel: str) -> float:      # pragma: no cover - souche
        raise NotImplementedError

    def channels(self) -> list[str]:                    # pragma: no cover - souche
        raise NotImplementedError
