"""Sondes DS18B20 sur bus 1-Wire.

Le modèle de sonde est confirmé, son interface avec le noyau aussi : le module
``w1-therm`` expose chaque sonde dans ``/sys/bus/w1/devices/<id>/``. Rien n'est
supposé au-delà — **MATERIEL À INTEGRER PLUS TARD** reste vrai pour le câblage
lui-même (broche, longueur de bus, résistance de tirage, alimentation : H-5).

Deux formats de lecture coexistent selon la version du noyau, et les deux sont
gérés :

* ``temperature`` — millidegrés, une ligne, disponible depuis Linux 5.x ;
* ``w1_slave`` — deux lignes, la première portant le résultat du contrôle de
  redondance (``crc=.. YES``), la seconde la valeur ``t=`` en millidegrés.

Le contrôle de redondance est vérifié quand il est disponible : sur un bus long
et secoué, une trame corrompue est plus probable qu'on ne le croit, et une
valeur fausse vaut moins qu'une absence de valeur.
"""

from __future__ import annotations

import time
from pathlib import Path

from ..interfaces import HardwareTimeout, SensorError, TemperatureSensor

#: Répertoire exposé par le module noyau ``w1-therm``.
W1_DEVICES_PATH = Path("/sys/bus/w1/devices")

#: Préfixe des identifiants de DS18B20 sur le bus 1-Wire.
DS18B20_PREFIX = "28-"

#: Valeur renvoyée par une sonde débranchée en cours de conversion.
DISCONNECTED_MILLIDEGREES = 85000

#: Plage physique de la DS18B20, d'après sa fiche technique.
SENSOR_RANGE_C = (-55.0, 125.0)


def scan_sensor_ids(root: Path = W1_DEVICES_PATH) -> list[str]:
    """Identifiants des sondes présentes sur le bus, liste vide si aucun bus.

    Ne lève jamais : sur un PC de développement, le noyau n'expose rien, et ce
    n'est pas une erreur.
    """
    try:
        return sorted(
            entry.name for entry in root.iterdir()
            if entry.name.startswith(DS18B20_PREFIX)
        )
    except OSError:
        return []


class DS18B20Sensor(TemperatureSensor):
    """Une sonde DS18B20, lue par l'interface noyau standard.

    La lecture est bornée : la conversion d'une DS18B20 dure environ 750 ms, et
    le pilote noyau peut mettre beaucoup plus longtemps si le bus est perturbé.
    Au-delà du délai imparti, la lecture abandonne au lieu de retenir le thread
    des températures — le chien de garde n'a alors rien à rattraper.
    """

    def __init__(self, sensor_id: str, *, timeout_s: float = 3.0,
                 root: Path = W1_DEVICES_PATH) -> None:
        self._sensor_id = sensor_id
        self._timeout_s = max(0.1, timeout_s)
        self._directory = root / sensor_id

    def sensor_id(self) -> str:
        return self._sensor_id

    def is_present(self) -> bool:
        """Vrai si la sonde est actuellement visible sur le bus."""
        try:
            return self._directory.is_dir()
        except OSError:
            return False

    def read_celsius(self) -> float:
        if not self.is_present():
            raise SensorError(f"sonde {self._sensor_id} absente du bus 1-Wire")

        started = time.monotonic()
        millidegrees = self._read_millidegrees(started)
        celsius = millidegrees / 1000.0

        low, high = SENSOR_RANGE_C
        if not (low <= celsius <= high):
            raise SensorError(
                f"sonde {self._sensor_id} : {celsius:.1f} °C hors des limites "
                f"de la DS18B20"
            )

        # 85,000 °C est la valeur d'initialisation du registre : une sonde qui
        # la renvoie n'a pas converti, elle n'est pas à 85 degrés.
        if millidegrees == DISCONNECTED_MILLIDEGREES:
            raise SensorError(
                f"sonde {self._sensor_id} : conversion non aboutie "
                "(valeur d'initialisation)"
            )
        return celsius

    # ------------------------------------------------------------------
    def _read_millidegrees(self, started: float) -> int:
        """Lit la valeur brute, en essayant les deux formats du noyau."""
        errors: list[str] = []

        temperature_file = self._directory / "temperature"
        if temperature_file.exists():
            try:
                return int(self._read_text(temperature_file, started).strip())
            except (OSError, ValueError) as exc:
                errors.append(f"temperature: {exc}")

        slave_file = self._directory / "w1_slave"
        if slave_file.exists():
            try:
                return self._parse_w1_slave(self._read_text(slave_file, started))
            except (OSError, ValueError, SensorError) as exc:
                errors.append(f"w1_slave: {exc}")

        detail = " ; ".join(errors) if errors else "aucun fichier de lecture"
        raise SensorError(f"sonde {self._sensor_id} illisible ({detail})")

    def _read_text(self, path: Path, started: float) -> str:
        """Lecture bornée dans le temps.

        La lecture sysfs elle-même n'est pas interruptible ; ce qui est vérifié,
        c'est qu'on n'enchaîne pas une seconde tentative au-delà de l'échéance.
        Le confinement d'une lecture réellement bloquée reste l'affaire du
        thread dédié et de son chien de garde.
        """
        if time.monotonic() - started > self._timeout_s:
            raise HardwareTimeout(
                f"sonde {self._sensor_id} : délai de {self._timeout_s:g} s dépassé"
            )
        return path.read_text(encoding="ascii")

    @staticmethod
    def _parse_w1_slave(content: str) -> int:
        lines = content.splitlines()
        if len(lines) < 2:
            raise SensorError("trame w1_slave incomplète")
        if not lines[0].rstrip().endswith("YES"):
            raise SensorError("somme de contrôle invalide")
        marker = lines[1].rfind("t=")
        if marker < 0:
            raise SensorError("trame w1_slave sans valeur")
        return int(lines[1][marker + 2:])

    def __repr__(self) -> str:      # pragma: no cover - confort de débogage
        return f"<DS18B20Sensor {self._sensor_id}>"
