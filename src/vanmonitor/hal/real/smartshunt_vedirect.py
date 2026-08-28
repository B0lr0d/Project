"""Liaison avec le Victron SmartShunt — VE.Direct filaire.

Chaîne retenue ::

    SmartShunt → VE.Direct filaire → interface VE.Direct/USB → port série

Le Bluetooth n'est pas retenu.

Implémentation prévue à l'**étape 6**. Deux points restent à préciser d'ici là,
et aucun ne bloque le reste du développement :

* la référence exacte de l'interface VE.Direct/USB ;
* le nom stable du port (``/dev/serial/by-id/…``), à figer par une règle udev
  pour ne pas dépendre de l'ordre d'énumération USB.

``pyserial`` est importé à l'intérieur du constructeur : son absence sur un PC
de développement ne doit pas empêcher le programme de démarrer.
"""

from __future__ import annotations

from ...models import BatteryReading
from ..interfaces import SmartShuntInterface


class VeDirectSmartShunt(SmartShuntInterface):
    """MATERIEL À INTEGRER PLUS TARD — implémentation prévue à l'étape 6."""

    def __init__(self, port: str | None, *, baudrate: int = 19200,
                 timeout_s: float = 2.0) -> None:
        raise NotImplementedError(
            "Liaison VE.Direct prévue à l'étape 6. "
            "Utiliser le mode simulation (--sim) en attendant."
        )

    def connect(self) -> None:                  # pragma: no cover - souche
        raise NotImplementedError

    def disconnect(self) -> None:               # pragma: no cover - souche
        raise NotImplementedError

    def read(self) -> BatteryReading:           # pragma: no cover - souche
        raise NotImplementedError

    def is_connected(self) -> bool:             # pragma: no cover - souche
        raise NotImplementedError
