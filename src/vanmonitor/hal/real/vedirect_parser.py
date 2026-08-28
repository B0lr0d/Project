"""Décodage des trames VE.Direct.

Volontairement séparé de la liaison série : ce module ne connaît ni port, ni
câble, ni matériel. Il se teste à partir de trames enregistrées, sans le
moindre SmartShunt branché — ce qui sera précieux à l'étape 6.

Implémentation prévue à l'**étape 6**. Les champs exacts et leurs unités seront
repris de la documentation VE.Direct de Victron et vérifiés au premier
branchement : rien n'est supposé ici.
"""

from __future__ import annotations

from ...models import BatteryReading


class VeDirectFrameError(ValueError):
    """Trame incomplète, tronquée ou dont la somme de contrôle est fausse."""


class VeDirectParser:
    """MATERIEL À INTEGRER PLUS TARD — implémentation prévue à l'étape 6."""

    def __init__(self) -> None:
        raise NotImplementedError(
            "Décodage VE.Direct prévu à l'étape 6 (intégration du SmartShunt)."
        )

    def feed(self, chunk: bytes) -> list[dict[str, str]]:   # pragma: no cover - souche
        """Accumule des octets et rend les trames complètes et valides."""
        raise NotImplementedError

    @staticmethod
    def to_reading(fields: dict[str, str]) -> BatteryReading:   # pragma: no cover
        """Traduit une trame décodée en ``BatteryReading``."""
        raise NotImplementedError
