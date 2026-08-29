"""Interfaces matérielles — le seul module que `core/` et `ui/` connaissent.

CONTRAT COMMUN À TOUS LES PILOTES
---------------------------------
Toute méthode de lecture ou de commande **rend la main avant le ``timeout_s``
reçu à la construction, ou lève ``HardwareTimeout``**. Aucune méthode ne boucle
en attendant un matériel absent, et aucune ne dort indéfiniment.

Ce contrat est ce qui permet à la couche d'acquisition de garantir qu'une
lecture lente ne bloque pas les autres : chaque famille de matériel tourne dans
son propre thread, et un pilote qui ne respecte pas son échéance est détecté
par le chien de garde (voir ``core/workers.py``).

Les pilotes ne convertissent rien : un capteur de niveau rend une valeur
**brute**, jamais des litres. La conversion est du ressort de la calibration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..constants import ConfirmedState, DisplayState, ValveCommand, ValveState
from ..models import BatteryReading


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class HardwareError(Exception):
    """Erreur matérielle rattrapable. Ne doit jamais faire tomber le programme."""


class HardwareTimeout(HardwareError):
    """Le matériel n'a pas répondu dans le délai imparti."""


class SensorError(HardwareError):
    """Capteur absent, muet ou renvoyant une valeur inexploitable."""


class LinkError(HardwareError):
    """Liaison de communication interrompue (port série, câble débranché…)."""


class ValveError(HardwareError):
    """Défaut d'un actionneur de clapet."""


# ---------------------------------------------------------------------------
# Capteurs
# ---------------------------------------------------------------------------

class TemperatureSensor(ABC):
    """Une sonde de température (DS18B20 sur le matériel réel)."""

    @abstractmethod
    def read_celsius(self) -> float:
        """Température en degrés Celsius.

        Lève ``SensorError`` si la sonde est absente ou incohérente,
        ``HardwareTimeout`` si elle ne répond pas à temps.
        """

    @abstractmethod
    def sensor_id(self) -> str:
        """Identifiant unique de la sonde, tel qu'affiché dans les Paramètres."""

    @abstractmethod
    def is_present(self) -> bool:
        """Vrai si la sonde est actuellement détectée sur le bus."""


class ADCInterface(ABC):
    """Convertisseur analogique-numérique.

    MATERIEL À INTEGRER PLUS TARD — modèle, bus et nombre de voies non choisis.
    """

    @abstractmethod
    def read_channel(self, channel: str) -> float:
        """Valeur brute d'une voie. Aucune unité physique n'est supposée."""

    @abstractmethod
    def channels(self) -> list[str]:
        """Noms des voies disponibles."""


class LevelSensor(ABC):
    """Capteur de niveau d'un réservoir.

    MATERIEL À INTEGRER PLUS TARD — technologie et signal de sortie non choisis.
    """

    @abstractmethod
    def read_raw(self) -> float:
        """Valeur **brute**, jamais des litres ni un pourcentage."""

    @abstractmethod
    def is_present(self) -> bool:
        ...


class SmartShuntInterface(ABC):
    """Accès aux données du Victron SmartShunt.

    Liaison retenue : VE.Direct filaire → interface VE.Direct/USB → port série.
    Cette interface reste abstraite pour que la liaison puisse changer sans
    toucher au reste du programme.
    """

    @abstractmethod
    def connect(self) -> None:
        """Ouvre la liaison. Lève ``LinkError`` en cas d'échec."""

    @abstractmethod
    def disconnect(self) -> None:
        """Ferme la liaison. Ne lève jamais."""

    @abstractmethod
    def read(self) -> BatteryReading:
        """Dernières mesures. Lève ``LinkError`` ou ``HardwareTimeout``."""

    @abstractmethod
    def is_connected(self) -> bool:
        ...


# ---------------------------------------------------------------------------
# Actionneurs
# ---------------------------------------------------------------------------

class DisplayPower(ABC):
    """Extinction et rallumage de l'affichage.

    La veille ne concerne **que la dalle**. Le Raspberry reste actif, les
    threads d'acquisition tournent, le chauffage régule et les alertes sont
    évaluées : rien de tout cela ne passe par cette interface.

    L'écran retenu est un Waveshare 5 pouces HDMI LCD (H) V4 : l'image arrive
    par HDMI, le tactile repart par USB. Les deux liaisons étant distinctes,
    couper l'image ne coupe pas le tactile — c'est ce qui permet de réveiller
    l'écran au doigt. La méthode exacte d'extinction dépend en revanche de la
    pile graphique en place, d'où cette abstraction.
    """

    @abstractmethod
    def sleep(self) -> None:
        """Éteint l'affichage. Lève ``HardwareError`` si la méthode échoue."""

    @abstractmethod
    def wake(self) -> None:
        """Rallume l'affichage."""

    @abstractmethod
    def state(self) -> DisplayState:
        """État de la dalle, ou ``INCONNU`` si la méthode ne sait pas le relire."""

    @abstractmethod
    def is_available(self) -> bool:
        """Faux si aucune méthode d'extinction n'a été trouvée sur ce système."""

    @abstractmethod
    def describe(self) -> str:
        """Nom de la méthode employée, pour le journal et le diagnostic."""


class ValveDriver(ABC):
    """Pilote d'un clapet de circuit de chauffage.

    MATERIEL À INTEGRER PLUS TARD — type d'actionneur non choisi, et surtout :
    **on ne sait pas encore s'il fournira un retour de position**.

    D'où la séparation stricte de trois notions :

    * ``get_commanded_state()`` — ce que le logiciel a demandé. Toujours connu.
    * ``get_confirmed_state()`` — ce que le matériel confirme. ``INCONNU`` tant
      qu'aucune position réelle n'est lue. Il est **interdit** d'y renvoyer une
      valeur déduite d'un ordre.
    * ``get_state()`` — vue synthétique pour l'affichage, qui n'est jamais
      présentée comme certaine sans confirmation.
    """

    @abstractmethod
    def open(self) -> None:
        ...

    @abstractmethod
    def close(self) -> None:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def get_commanded_state(self) -> ValveCommand:
        ...

    @abstractmethod
    def has_position_feedback(self) -> bool:
        """Faux tant que le matériel choisi ne fournit pas de retour de position."""

    @abstractmethod
    def get_confirmed_state(self) -> ConfirmedState:
        ...

    @abstractmethod
    def get_state(self) -> ValveState:
        ...

    @abstractmethod
    def has_fault(self) -> bool:
        ...
