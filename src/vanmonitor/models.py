"""Structures de données immuables échangées entre les couches.

Aucune dépendance : ni Qt, ni matériel.

Toutes les dates (``updated_at``, ``since``…) sont exprimées sur l'horloge
**monotone** (``util.timebase.monotonic``), jamais sur l'heure murale : le
Raspberry n'a ni Internet ni horloge temps réel, son heure murale peut être
fausse au démarrage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .constants import (
    AlertLevel,
    CircuitId,
    ConfirmedState,
    HeatingMode,
    SensorLossFallback,
    Status,
    TankId,
    ValveCommand,
    ValveState,
    ZoneId,
)

__all__ = [
    "AcquisitionSnapshot",
    "Alert",
    "BatteryReading",
    "CircuitStatus",
    "Sample",
    "SystemSnapshot",
    "TankReading",
    "DisplayStatus",
    "TemperatureReading",
    "ValveObservation",
    "WorkerHealth",
]


@dataclass(frozen=True)
class Sample:
    """Valeur brute acquise par un thread de matériel, avec son état de santé.

    C'est le seul objet que les threads d'acquisition publient. Les services
    métier le transforment ensuite en grandeurs affichables.
    """

    value: Any | None
    status: Status
    updated_at: float | None       # horloge monotone, None si jamais lue
    age_s: float | None            # âge au moment de la lecture, None si jamais lue
    reason: str | None = None      # message de la dernière erreur, si FAULT

    @property
    def ok(self) -> bool:
        return self.status is Status.OK


@dataclass(frozen=True)
class ValveObservation:
    """Ce que la couche d'acquisition sait d'un clapet, sans aucune supposition.

    ``confirmed`` vaut ``INCONNU`` dès que le matériel ne renvoie pas de
    position réelle, et ``state_is_certain`` est alors faux : c'est ce champ,
    et lui seul, qui autorise l'interface à afficher un état sec plutôt qu'un
    état « commandé ».
    """

    commanded: ValveCommand
    confirmed: ConfirmedState
    feedback_available: bool
    display_state: ValveState
    fault: bool
    status: Status
    updated_at: float | None = None
    reason: str | None = None

    @property
    def state_is_certain(self) -> bool:
        return self.feedback_available and self.confirmed is not ConfirmedState.INCONNU


@dataclass(frozen=True)
class TemperatureReading:
    zone: ZoneId
    label: str
    celsius: float | None
    status: Status
    updated_at: float | None
    reason: str | None = None


@dataclass(frozen=True)
class TankReading:
    tank: TankId
    label: str
    litres: float | None           # None pour les eaux grises (calibrées en %)
    percent: float | None          # None tant que la capacité n'est pas connue
    raw: float | None
    status: Status
    out_of_range: bool = False
    calibrated: bool = False
    updated_at: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class BatteryReading:
    soc_percent: float | None = None
    voltage_v: float | None = None
    current_a: float | None = None
    power_w: float | None = None
    consumed_ah: float | None = None
    time_to_go_min: int | None = None     # None si absente ou jugée non fiable
    status: Status = Status.ABSENT
    updated_at: float | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CircuitStatus:
    """État d'un circuit de chauffage.

    ``commanded`` et ``confirmed`` ne sont jamais confondus : sans retour de
    position, ``confirmed`` vaut ``INCONNU`` et ``state_is_certain`` vaut
    ``False``, ce qui oblige l'interface à écrire « commandé ».
    """

    circuit: CircuitId
    label: str
    mode: HeatingMode
    zone: ZoneId
    temperature_c: float | None

    commanded: ValveCommand
    confirmed: ConfirmedState
    feedback_available: bool
    display_state: ValveState
    state_is_certain: bool
    commanded_since: float | None = None
    transition_deadline: float | None = None

    open_below_c: float | None = None
    close_above_c: float | None = None
    thresholds_defined: bool = False
    on_sensor_loss: SensorLossFallback = SensorLossFallback.HOLD
    fallback_active: bool = False

    fault: bool = False
    fault_reason: str | None = None


@dataclass(frozen=True)
class Alert:
    key: str
    level: AlertLevel
    message: str
    active_since: float


@dataclass(frozen=True)
class WorkerHealth:
    """Santé d'un thread d'acquisition, telle que vue par le superviseur."""

    name: str
    last_success: float | None
    consecutive_failures: int
    stuck: bool
    restarts: int
    running: bool
    last_error: str | None = None


@dataclass(frozen=True)
class DisplayStatus:
    """État de la veille d'écran, tel que l'interface a besoin de le connaître.

    ``asleep`` dit ce que le programme a commandé et cru obtenir ; si la
    méthode d'extinction a échoué, ``last_error`` le dit et ``asleep`` reste
    faux — se croire endormi ferait avaler le prochain toucher pour rien.
    """

    asleep: bool
    enabled: bool
    available: bool
    delay_s: float
    idle_s: float
    method: str
    last_error: str | None = None


@dataclass(frozen=True)
class AcquisitionSnapshot:
    """Ce que les threads d'acquisition ont lu, sans interprétation métier.

    C'est le livrable de l'étape 2 : des valeurs **brutes** datées, avec l'état
    de santé de chaque thread. Les étapes suivantes construiront le
    ``SystemSnapshot`` à partir de là (calibration, hystérésis, alertes).
    """

    timestamp: float
    temperatures: dict[ZoneId, Sample] = field(default_factory=dict)
    levels: dict[TankId, Sample] = field(default_factory=dict)
    battery: Sample = field(
        default_factory=lambda: Sample(None, Status.ABSENT, None, None)
    )
    #: La valeur de chaque échantillon est une ``ValveObservation``, ou ``None``
    #: tant que le clapet n'a pas encore été lu — ce que le statut distingue de
    #: l'absence d'actionneur.
    valves: dict[CircuitId, Sample] = field(default_factory=dict)
    workers: tuple[WorkerHealth, ...] = ()
    #: Identifiants réellement détectés sur le bus 1-Wire au dernier balayage.
    available_sensor_ids: tuple[str, ...] = ()
    #: Température lue par identifiant de sonde. Sert à l'identification
    #: physique dans les Paramètres : on réchauffe une sonde et on regarde
    #: laquelle bouge. Ne contient les sondes non associées que lorsque le mode
    #: identification est actif, pour ne pas charger le bus en permanence.
    sensor_temperatures: dict[str, Sample] = field(default_factory=dict)
    simulation: bool = False


@dataclass(frozen=True)
class SystemSnapshot:
    """Photographie complète de l'installation, publiée vers l'interface.

    Assemblée par la boucle de contrôle (étape 3 et suivantes). À l'étape 2,
    seule la couche d'acquisition existe et publie des ``Sample``.
    """

    timestamp: float
    temperatures: dict[ZoneId, TemperatureReading] = field(default_factory=dict)
    tanks: dict[TankId, TankReading] = field(default_factory=dict)
    battery: BatteryReading = field(default_factory=BatteryReading)
    circuits: dict[CircuitId, CircuitStatus] = field(default_factory=dict)
    alerts: tuple[Alert, ...] = ()
    #: Sondes détectées sur le bus, et leur température par identifiant.
    #: Alimentent la section Sondes des Paramètres.
    available_sensor_ids: tuple[str, ...] = ()
    sensor_temperatures: dict[str, Sample] = field(default_factory=dict)
    #: État de la veille d'écran. ``None`` quand elle n'est pas gérée.
    display: DisplayStatus | None = None
    simulation: bool = False
