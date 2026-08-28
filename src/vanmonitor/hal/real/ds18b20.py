"""Sondes DS18B20 sur bus 1-Wire.

Implémentation prévue à l'**étape 4**. Le principe est déjà arrêté : lecture
via l'interface noyau standard ``/sys/bus/w1/devices/<id>/temperature``, sous
échéance, dans le thread dédié aux températures.

MATERIEL À INTEGRER PLUS TARD : broche utilisée, longueur du bus, résistance de
tirage et alimentation ne sont pas encore définis (H-5).
"""

from __future__ import annotations

from pathlib import Path

from ..interfaces import TemperatureSensor

#: Répertoire exposé par le module noyau ``w1-therm``.
W1_DEVICES_PATH = Path("/sys/bus/w1/devices")

#: Préfixe des identifiants de DS18B20 sur le bus 1-Wire.
DS18B20_PREFIX = "28-"


def scan_sensor_ids(root: Path = W1_DEVICES_PATH) -> list[str]:
    """Identifiants des sondes présentes sur le bus, liste vide si aucun bus.

    Utilisable dès maintenant : ne lève pas si le noyau n'expose rien, ce qui
    est le cas sur un PC de développement.
    """
    try:
        return sorted(
            entry.name for entry in root.iterdir()
            if entry.name.startswith(DS18B20_PREFIX)
        )
    except OSError:
        return []


class DS18B20Sensor(TemperatureSensor):
    """MATERIEL À INTEGRER PLUS TARD — implémentation prévue à l'étape 4."""

    def __init__(self, sensor_id: str, *, timeout_s: float = 3.0) -> None:
        raise NotImplementedError(
            "MATERIEL À INTEGRER PLUS TARD : lecture 1-Wire prévue à l'étape 4. "
            "Utiliser le mode simulation (--sim) en attendant."
        )

    def read_celsius(self) -> float:            # pragma: no cover - souche
        raise NotImplementedError

    def sensor_id(self) -> str:                 # pragma: no cover - souche
        raise NotImplementedError

    def is_present(self) -> bool:               # pragma: no cover - souche
        raise NotImplementedError
