"""Énumérations partagées par toutes les couches.

Aucune dépendance : ni Qt, ni matériel, ni configuration.

La valeur de chaque membre est la clé utilisée dans le fichier de configuration
(`config.json`), afin qu'aucune table de correspondance ne soit nécessaire.
"""

from __future__ import annotations

from enum import Enum

APP_NAME = "vanmonitor"


class Status(Enum):
    """État d'une grandeur mesurée, tel qu'affiché à l'écran."""

    OK = "ok"
    STALE = "stale"       # valeur trop ancienne
    FAULT = "fault"       # erreur de lecture
    ABSENT = "absent"     # capteur non configuré ou non détecté


class HeatingMode(Enum):
    AUTO = "auto"
    MANUEL = "manuel"


class ZoneId(Enum):
    """Les cinq zones de température. L'ordre est celui de l'affichage."""

    LOCAL_BATTERIE = "local_batterie"
    LOCAL_EAU = "local_eau"
    COFFRE = "coffre"
    CABINE = "cabine"
    CELLULE = "cellule"


class CircuitId(Enum):
    """Les trois circuits de chauffage. Jamais « Circuit 1/2/3 »."""

    LOCAL_EAU = "local_eau"
    LOCAL_BATTERIE = "local_batterie"
    CABINE = "cabine"


class TankId(Enum):
    EAU_PROPRE = "eau_propre"
    EAUX_GRISES = "eaux_grises"
    GASOIL = "gasoil"


class AlertLevel(Enum):
    INFO = "info"
    WARN = "warn"
    CRITIQUE = "critique"


class SensorLossFallback(Enum):
    """Repli appliqué à un circuit dont la température n'est plus fiable.

    Réglage de sécurité : modifiable depuis la page Paramètres, mais seulement
    après confirmation explicite de l'utilisateur.
    """

    OPEN = "open"       # OUVRIR
    CLOSE = "close"     # FERMER
    HOLD = "hold"       # MAINTENIR le dernier état


# --------------------------------------------------------------------------
# Clapets : trois notions distinctes, jamais confondues.
# --------------------------------------------------------------------------

class ValveCommand(Enum):
    """Ce que le logiciel a demandé au matériel. Toujours connu."""

    OPEN = "open"
    CLOSE = "close"
    STOP = "stop"
    NONE = "none"       # aucun ordre encore transmis


class ConfirmedState(Enum):
    """Ce que le matériel confirme réellement.

    Un pilote sans retour de position renvoie TOUJOURS ``INCONNU``.
    Il est interdit d'y placer une valeur déduite d'un ordre.
    """

    OUVERT = "ouvert"
    FERME = "ferme"
    INCONNU = "inconnu"


class ValveState(Enum):
    """État affiché à l'écran, dérivé de la commande et de la confirmation."""

    OUVERT = "ouvert"
    FERME = "ferme"
    OUVERTURE = "ouverture"
    FERMETURE = "fermeture"
    ERREUR = "erreur"
    INCONNU = "inconnu"


# --------------------------------------------------------------------------
# Ordres d'affichage figés (l'écran ne réordonne jamais ces listes).
# --------------------------------------------------------------------------

ZONE_ORDER: tuple[ZoneId, ...] = (
    ZoneId.LOCAL_BATTERIE,
    ZoneId.LOCAL_EAU,
    ZoneId.COFFRE,
    ZoneId.CABINE,
    ZoneId.CELLULE,
)

CIRCUIT_ORDER: tuple[CircuitId, ...] = (
    CircuitId.LOCAL_EAU,
    CircuitId.LOCAL_BATTERIE,
    CircuitId.CABINE,
)

TANK_ORDER: tuple[TankId, ...] = (
    TankId.EAU_PROPRE,
    TankId.EAUX_GRISES,
    TankId.GASOIL,
)

#: Zone de température associée à chaque circuit de chauffage.
#: La configuration peut la redéfinir ; ceci n'est que la valeur de départ.
CIRCUIT_DEFAULT_ZONE: dict[CircuitId, ZoneId] = {
    CircuitId.LOCAL_EAU: ZoneId.LOCAL_EAU,
    CircuitId.LOCAL_BATTERIE: ZoneId.LOCAL_BATTERIE,
    CircuitId.CABINE: ZoneId.CABINE,
}

#: Libellés de repli utilisés dans l'interface et les journaux.
FALLBACK_LABELS: dict[SensorLossFallback, str] = {
    SensorLossFallback.OPEN: "OUVRIR",
    SensorLossFallback.CLOSE: "FERMER",
    SensorLossFallback.HOLD: "MAINTENIR",
}
