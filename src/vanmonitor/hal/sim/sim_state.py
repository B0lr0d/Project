"""État du monde simulé, partagé par tous les mocks.

C'est le « fourgon virtuel » : les mocks lisent ici ce que le matériel réel
lirait sur ses capteurs. Le panneau de simulation (``ui/sim_panel.py``) écrit
dans cet objet, et rien d'autre ne doit le faire.

Toutes les méthodes sont utilisables depuis plusieurs threads.

Le simulateur ne simule pas seulement le fonctionnement nominal : il sait aussi
**tomber en panne**, c'est même sa raison d'être. Les cinq modes de panne
couvrent ce que le matériel réel peut faire subir au programme.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from ...constants import CircuitId, TankId, ValveCommand, ZoneId
from ...util.timebase import monotonic


class FaultMode(Enum):
    """Comportement simulé d'un équipement."""

    OK = "ok"
    #: Non détecté : ``is_present()`` faux, lecture en erreur.
    ABSENT = "absent"
    #: Détecté mais renvoyant une erreur de lecture.
    ERROR = "error"
    #: Trop lent : dépasse le délai et lève proprement ``HardwareTimeout``.
    SLOW = "slow"
    #: Bloqué : ignore le délai. Sert à éprouver le chien de garde (risque R-16).
    STUCK = "stuck"


FAULT_LABELS: dict[FaultMode, str] = {
    FaultMode.OK: "Normal",
    FaultMode.ABSENT: "Absent",
    FaultMode.ERROR: "Erreur de lecture",
    FaultMode.SLOW: "Lent (dépasse le délai)",
    FaultMode.STUCK: "Bloqué (chien de garde)",
}

#: Identifiants 1-Wire simulés, stables d'une exécution à l'autre.
SIM_SENSOR_IDS: dict[ZoneId, str] = {
    ZoneId.LOCAL_BATTERIE: "28-SIM0000000001",
    ZoneId.LOCAL_EAU: "28-SIM0000000002",
    ZoneId.COFFRE: "28-SIM0000000003",
    ZoneId.CABINE: "28-SIM0000000004",
    ZoneId.CELLULE: "28-SIM0000000005",
}

#: Valeurs de départ « une nuit fraîche en montagne ».
_DEFAULT_TEMPERATURES: dict[ZoneId, float] = {
    ZoneId.LOCAL_BATTERIE: 12.4,
    ZoneId.LOCAL_EAU: 6.1,
    ZoneId.COFFRE: 9.8,
    ZoneId.CABINE: 18.2,
    ZoneId.CELLULE: 19.0,
}

#: Niveaux bruts, sans unité : c'est la calibration qui leur donnera un sens.
_DEFAULT_LEVELS: dict[TankId, float] = {
    TankId.EAU_PROPRE: 0.68,
    TankId.EAUX_GRISES: 0.42,
    TankId.GASOIL: 0.72,
}


@dataclass
class SimBattery:
    soc_percent: float = 87.0
    voltage_v: float = 13.2
    current_a: float = -4.2
    consumed_ah: float = -12.0
    time_to_go_min: int | None = 1080
    time_to_go_available: bool = True
    #: Recalcule l'autonomie à partir de la charge restante et du courant,
    #: comme le ferait un vrai SmartShunt. Sans cela, baisser l'état de charge
    #: dans le panneau de simulation laisserait une autonomie inchangée, ce qui
    #: donnerait à croire à un affichage figé.
    time_to_go_auto: bool = True

    @property
    def power_w(self) -> float:
        return self.voltage_v * self.current_a

    def computed_time_to_go_min(self) -> int | None:
        """Autonomie déduite de l'état de charge et du courant de décharge.

        Un SmartShunt ne l'annonce pas en charge ni à courant nul : il rend
        alors « infini », que l'interface traite comme une valeur absente.
        """
        if self.current_a >= -0.05:
            return None                 # en charge ou à l'arrêt
        # Capacité totale déduite de ce qui a été consommé pour arriver ici.
        missing = 1.0 - self.soc_percent / 100.0
        if missing <= 0.01 or self.consumed_ah >= 0:
            return None
        capacity_ah = abs(self.consumed_ah) / missing
        remaining_ah = capacity_ah * self.soc_percent / 100.0
        return max(0, int(remaining_ah / abs(self.current_a) * 60))


@dataclass
class SimValve:
    """Clapet simulé, avec un temps de course réaliste.

    ``feedback`` reproduit l'inconnue matérielle centrale : selon l'actionneur
    qui sera retenu, une position réelle sera lisible… ou pas du tout.
    """

    feedback: bool = False
    fault: bool = False
    travel_time_s: float = 6.0
    commanded: ValveCommand = ValveCommand.NONE
    #: Position physique réelle : 0.0 fermé, 1.0 ouvert.
    position: float = 0.0
    target: float = 0.0
    updated_at: float = field(default_factory=monotonic)

    def advance(self, now: float) -> None:
        """Fait avancer la course du clapet jusqu'à l'instant ``now``."""
        elapsed = max(0.0, now - self.updated_at)
        self.updated_at = now
        if self.travel_time_s <= 0:
            self.position = self.target
            return
        step = elapsed / self.travel_time_s
        if self.position < self.target:
            self.position = min(self.target, self.position + step)
        elif self.position > self.target:
            self.position = max(self.target, self.position - step)

    @property
    def moving(self) -> bool:
        return abs(self.position - self.target) > 1e-6


class SimState:
    """Le monde simulé. Un seul exemplaire par exécution."""

    def __init__(self) -> None:
        self._lock = threading.RLock()

        self._temperatures = dict(_DEFAULT_TEMPERATURES)
        self._temp_faults = {zone: FaultMode.OK for zone in ZoneId}

        self._levels = dict(_DEFAULT_LEVELS)
        self._level_faults = {tank: FaultMode.OK for tank in TankId}

        self._battery = SimBattery()
        self._battery_fault = FaultMode.OK

        self._valves = {circuit: SimValve() for circuit in CircuitId}
        # Un clapet équipé d'un retour de position, deux sans : les deux cas
        # doivent être visibles dès le premier lancement.
        self._valves[CircuitId.LOCAL_EAU].feedback = True

        #: Facteur appliqué aux temporisations simulées. Réduit dans les tests.
        self._time_scale = 1.0

    # ------------------------------------------------------------------
    # Températures
    # ------------------------------------------------------------------
    def temperature(self, zone: ZoneId) -> float:
        with self._lock:
            return self._temperatures[zone]

    def set_temperature(self, zone: ZoneId, celsius: float) -> None:
        with self._lock:
            self._temperatures[zone] = float(celsius)

    def temperature_fault(self, zone: ZoneId) -> FaultMode:
        with self._lock:
            return self._temp_faults[zone]

    def set_temperature_fault(self, zone: ZoneId, mode: FaultMode) -> None:
        with self._lock:
            self._temp_faults[zone] = mode

    def temperatures(self) -> dict[ZoneId, float]:
        with self._lock:
            return dict(self._temperatures)

    def present_sensor_ids(self) -> list[str]:
        """Sondes actuellement visibles sur le bus 1-Wire simulé.

        Une sonde mise en panne « Absent » disparaît du bus, exactement comme
        une sonde débranchée : la section Sondes des Paramètres doit pouvoir le
        montrer sans qu'on aille bricoler un fichier système.
        """
        with self._lock:
            return [
                SIM_SENSOR_IDS[zone] for zone in ZoneId
                if self._temp_faults[zone] is not FaultMode.ABSENT
            ]

    def zone_of_sensor(self, sensor_id: str) -> ZoneId | None:
        """Zone du fourgon virtuel où se trouve physiquement cette sonde."""
        for zone, simulated_id in SIM_SENSOR_IDS.items():
            if simulated_id == sensor_id:
                return zone
        return None

    # ------------------------------------------------------------------
    # Niveaux
    # ------------------------------------------------------------------
    def level(self, tank: TankId) -> float:
        with self._lock:
            return self._levels[tank]

    def set_level(self, tank: TankId, raw: float) -> None:
        with self._lock:
            self._levels[tank] = float(raw)

    def level_fault(self, tank: TankId) -> FaultMode:
        with self._lock:
            return self._level_faults[tank]

    def set_level_fault(self, tank: TankId, mode: FaultMode) -> None:
        with self._lock:
            self._level_faults[tank] = mode

    def levels(self) -> dict[TankId, float]:
        with self._lock:
            return dict(self._levels)

    # ------------------------------------------------------------------
    # Batterie
    # ------------------------------------------------------------------
    def battery(self) -> SimBattery:
        with self._lock:
            battery = SimBattery(**vars(self._battery))
        if battery.time_to_go_auto:
            battery.time_to_go_min = battery.computed_time_to_go_min()
        return battery

    def update_battery(self, **fields: object) -> None:
        with self._lock:
            for key, value in fields.items():
                if not hasattr(self._battery, key):
                    raise KeyError(f"champ batterie inconnu : {key}")
                setattr(self._battery, key, value)

    def battery_fault(self) -> FaultMode:
        with self._lock:
            return self._battery_fault

    def set_battery_fault(self, mode: FaultMode) -> None:
        with self._lock:
            self._battery_fault = mode

    # ------------------------------------------------------------------
    # Clapets
    # ------------------------------------------------------------------
    def valve(self, circuit: CircuitId) -> SimValve:
        """Copie de l'état du clapet, course avancée jusqu'à maintenant."""
        with self._lock:
            valve = self._valves[circuit]
            valve.advance(monotonic())
            return SimValve(**vars(valve))

    def command_valve(self, circuit: CircuitId, command: ValveCommand) -> None:
        """Applique un ordre au clapet simulé (appelé par ``MockValveDriver``)."""
        with self._lock:
            valve = self._valves[circuit]
            valve.advance(monotonic())
            valve.commanded = command
            if command is ValveCommand.OPEN:
                valve.target = 1.0
            elif command is ValveCommand.CLOSE:
                valve.target = 0.0
            elif command is ValveCommand.STOP:
                valve.target = valve.position

    def set_valve_feedback(self, circuit: CircuitId, available: bool) -> None:
        with self._lock:
            self._valves[circuit].feedback = bool(available)

    def set_valve_fault(self, circuit: CircuitId, fault: bool) -> None:
        with self._lock:
            self._valves[circuit].fault = bool(fault)

    def set_valve_travel_time(self, circuit: CircuitId, seconds: float) -> None:
        with self._lock:
            self._valves[circuit].travel_time_s = max(0.0, float(seconds))

    # ------------------------------------------------------------------
    # Temporisations simulées
    # ------------------------------------------------------------------
    @property
    def time_scale(self) -> float:
        with self._lock:
            return self._time_scale

    def set_time_scale(self, scale: float) -> None:
        with self._lock:
            self._time_scale = max(0.0, float(scale))

    def reset_faults(self) -> None:
        """Rétablit tous les équipements en fonctionnement normal."""
        with self._lock:
            for zone in ZoneId:
                self._temp_faults[zone] = FaultMode.OK
            for tank in TankId:
                self._level_faults[tank] = FaultMode.OK
            self._battery_fault = FaultMode.OK
            for valve in self._valves.values():
                valve.fault = False


def apply_fault_mode(
    mode: FaultMode,
    timeout_s: float,
    *,
    still_faulty: "Callable[[], bool]",
    error: "Callable[[str], Exception]",
    label: str,
    scale: float = 1.0,
) -> None:
    """Traduit un mode de panne simulé en comportement matériel réaliste.

    Ne fait rien en mode ``OK``. Sinon, lève l'exception attendue — après avoir
    consommé le temps qu'un vrai matériel aurait consommé.

    Le mode ``STUCK`` reproduit le seul cas que le programme ne peut pas
    interrompre : un pilote qui ne rend jamais la main. Il attend donc que le
    panneau de simulation rétablisse l'équipement, en vérifiant régulièrement,
    afin de rester débloquable ; c'est le chien de garde qui doit réagir, pas
    la lecture elle-même.
    """
    from ..interfaces import HardwareTimeout        # import local : évite un cycle

    if mode is FaultMode.OK:
        return

    if mode is FaultMode.ABSENT:
        raise error(f"{label} : non détecté")

    if mode is FaultMode.ERROR:
        raise error(f"{label} : erreur de lecture")

    if mode is FaultMode.SLOW:
        # Dépasse volontairement le délai, mais le respecte : le pilote rend
        # la main en signalant lui-même le dépassement.
        _sleep(min(timeout_s, 5.0) * scale, still_faulty)
        raise HardwareTimeout(f"{label} : pas de réponse en {timeout_s:g} s")

    if mode is FaultMode.STUCK:
        _sleep(_STUCK_MAX_WAIT_S, still_faulty)
        raise HardwareTimeout(f"{label} : lecture bloquée")


#: Une lecture « bloquée » finit tout de même par abandonner : un thread qui ne
#: se termine jamais ne pourrait pas être arrêté proprement à la fermeture.
_STUCK_MAX_WAIT_S = 3600.0

_POLL_STEP_S = 0.05


def _sleep(seconds: float, still_faulty: "Callable[[], bool]") -> None:
    """Attend ``seconds``, en s'interrompant si la panne est levée entre-temps."""
    deadline = monotonic() + seconds
    while monotonic() < deadline:
        if not still_faulty():
            return
        time.sleep(_POLL_STEP_S)
