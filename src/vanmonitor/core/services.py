"""Transformation des mesures brutes en grandeurs affichables.

Ces services ne touchent aucun matériel : ils lisent l'instantané d'acquisition
(déjà en mémoire) et le traduisent dans le vocabulaire de l'écran — degrés,
litres, pourcentages, états de circuits.

Répartition avec les étapes suivantes, pour mémoire :

* **étape 4** — décalage de sonde par zone, association et identification
  depuis les Paramètres (le service est ici, l'outillage vient là) ;
* **étape 5** — filtrage des mesures de niveau (médian puis moyenne
  exponentielle) et assistant de calibration complet ;
* **étape 6** — liaison VE.Direct réelle ; l'assemblage de la lecture batterie
  ne changera pas ;
* **étape 7** — régulation automatique du chauffage : hystérésis, durée
  minimale de maintien, expiration de transition, activation du repli. Ici,
  seuls l'état affiché et les commandes manuelles sont traités.

(Fichier ajouté : il regroupe ce que l'arborescence répartissait entre
``temperature_service.py``, ``tank_service.py`` et ``battery_service.py``.
Chacun ne faisant qu'une trentaine de lignes, trois fichiers auraient coûté
plus de navigation qu'ils n'auraient apporté de clarté. Voir §13 du document
d'architecture.)
"""

from __future__ import annotations

from ..config import ConfigStore
from ..constants import (
    CIRCUIT_ORDER,
    CircuitId,
    ConfirmedState,
    HeatingMode,
    SensorLossFallback,
    Status,
    TANK_ORDER,
    TankId,
    ValveCommand,
    ValveState,
    ZONE_ORDER,
    ZoneId,
)
from ..models import (
    AcquisitionSnapshot,
    BatteryReading,
    CircuitStatus,
    Sample,
    TankReading,
    TemperatureReading,
    ValveObservation,
)
from ..util.logging_setup import get_logger
from .calibration import CalibrationError, CalibrationTable, demo_table

logger = get_logger("core.services")


# ---------------------------------------------------------------------------
# Températures
# ---------------------------------------------------------------------------

class TemperatureService:
    """Mesures brutes → températures affichables, zone par zone."""

    def __init__(self, config: ConfigStore) -> None:
        self._config = config

    def label(self, zone: ZoneId) -> str:
        return str(self._config.get(f"temperatures.zones.{zone.value}.label", zone.value))

    def build(self, samples: dict[ZoneId, Sample]) -> dict[ZoneId, TemperatureReading]:
        readings: dict[ZoneId, TemperatureReading] = {}
        for zone in ZONE_ORDER:
            sample = samples.get(zone)
            offset = float(self._config.get(f"temperatures.zones.{zone.value}.offset_c", 0.0))
            celsius = None
            if sample is not None and sample.ok and sample.value is not None:
                celsius = float(sample.value) + offset
            readings[zone] = TemperatureReading(
                zone=zone,
                label=self.label(zone),
                celsius=celsius,
                status=sample.status if sample else Status.ABSENT,
                updated_at=sample.updated_at if sample else None,
                reason=sample.reason if sample else None,
            )
        return readings


# ---------------------------------------------------------------------------
# Réservoirs
# ---------------------------------------------------------------------------

class TankService:
    """Mesures brutes → litres et pourcentages, via la calibration."""

    def __init__(self, config: ConfigStore, *, simulation: bool = False) -> None:
        self._config = config
        self._simulation = simulation
        self._warned_demo = False

    def label(self, tank: TankId) -> str:
        return str(self._config.get(f"tanks.{tank.value}.label", tank.value))

    def table(self, tank: TankId) -> CalibrationTable:
        """Table de calibration configurée pour ce réservoir."""
        return CalibrationTable.from_config(self._config.section(f"tanks.{tank.value}"))

    def effective_table(self, tank: TankId) -> CalibrationTable:
        """Table réellement utilisée pour l'affichage.

        En simulation et seulement là, un réservoir non calibré reçoit une
        table de démonstration : l'écran doit pouvoir être jugé sur des valeurs
        plausibles avant que le matériel existe. Rien n'est écrit dans la
        configuration, et le matériel réel n'en bénéficie jamais.
        """
        table = self.table(tank)
        if table.is_valid() or not self._simulation:
            return table
        if not self._warned_demo:
            logger.info("simulation : calibration de démonstration pour les réservoirs")
            self._warned_demo = True
        section = self._config.section(f"tanks.{tank.value}")
        return demo_table(section.get("unit", "litres"), section.get("capacity_l"))

    def save_table(self, tank: TankId, table: CalibrationTable) -> None:
        """Enregistre une table, après validation. Lève ``CalibrationError``."""
        table.validate()
        self._config.set(f"tanks.{tank.value}.calibration.points",
                         table.to_config()["points"])

    def shows_litres(self, tank: TankId) -> bool:
        return "litres" in (self._config.get(f"tanks.{tank.value}.display") or [])

    def build(self, samples: dict[TankId, Sample]) -> dict[TankId, TankReading]:
        readings: dict[TankId, TankReading] = {}
        for tank in TANK_ORDER:
            sample = samples.get(tank)
            readings[tank] = self._build_one(tank, sample)
        return readings

    def _build_one(self, tank: TankId, sample: Sample | None) -> TankReading:
        label = self.label(tank)
        status = sample.status if sample else Status.ABSENT
        raw = sample.value if sample and sample.ok else None

        if raw is None:
            return TankReading(
                tank=tank, label=label, litres=None, percent=None, raw=None,
                status=status, calibrated=self.effective_table(tank).is_valid(),
                updated_at=sample.updated_at if sample else None,
                reason=sample.reason if sample else None,
            )

        table = self.effective_table(tank)
        if not table.is_valid():
            # Capteur sain mais réservoir non calibré : l'écran affiche `--`,
            # ce qui est exact. Ce n'est pas une panne.
            return TankReading(
                tank=tank, label=label, litres=None, percent=None, raw=float(raw),
                status=status, calibrated=False,
                updated_at=sample.updated_at, reason="réservoir non calibré",
            )

        try:
            litres, out_of_range = table.litres(float(raw))
            percent, _ = table.percent(float(raw))
        except CalibrationError as exc:
            return TankReading(
                tank=tank, label=label, litres=None, percent=None, raw=float(raw),
                status=Status.FAULT, calibrated=False,
                updated_at=sample.updated_at, reason=str(exc),
            )

        return TankReading(
            tank=tank, label=label,
            litres=litres if self.shows_litres(tank) else None,
            percent=percent, raw=float(raw), status=status,
            out_of_range=out_of_range, calibrated=True,
            updated_at=sample.updated_at,
        )


# ---------------------------------------------------------------------------
# Batterie
# ---------------------------------------------------------------------------

class BatteryService:
    """Lecture du SmartShunt → grandeurs affichables."""

    def __init__(self, config: ConfigStore) -> None:
        self._config = config

    def build(self, sample: Sample) -> BatteryReading:
        if not sample.ok or sample.value is None:
            return BatteryReading(
                status=sample.status, updated_at=sample.updated_at,
                reason=sample.reason,
            )

        reading: BatteryReading = sample.value
        time_to_go = reading.time_to_go_min

        # L'autonomie n'est affichée que si elle est fournie ET plausible :
        # une valeur aberrante vaut moins que pas de valeur du tout.
        if not bool(self._config.get("battery.show_time_to_go", True)):
            time_to_go = None
        elif time_to_go is not None:
            maximum = float(self._config.get("battery.time_to_go_max_valid_min", 6000))
            if time_to_go <= 0 or time_to_go > maximum:
                time_to_go = None

        return BatteryReading(
            soc_percent=reading.soc_percent,
            voltage_v=reading.voltage_v,
            current_a=reading.current_a,
            power_w=reading.power_w,
            consumed_ah=reading.consumed_ah,
            time_to_go_min=time_to_go,
            status=sample.status,
            updated_at=sample.updated_at,
        )


# ---------------------------------------------------------------------------
# Chauffage
# ---------------------------------------------------------------------------

class HeatingService:
    """État des circuits de chauffage.

    Étape 3 : assemblage de l'état affiché, modes, seuils, repli, commandes
    manuelles. **La régulation automatique (hystérésis, durée minimale de
    maintien, expiration de transition, déclenchement du repli) est l'étape 7**
    et n'est pas encore active : un circuit en AUTO conserve son état, l'écran
    n'affiche donc rien de faux.
    """

    def __init__(self, config: ConfigStore) -> None:
        self._config = config

    # -- lecture de la configuration ------------------------------------
    def label(self, circuit: CircuitId) -> str:
        return str(self._config.get(f"heating.circuits.{circuit.value}.label",
                                    circuit.value))

    def zone(self, circuit: CircuitId) -> ZoneId:
        raw = self._config.get(f"heating.circuits.{circuit.value}.zone", circuit.value)
        try:
            return ZoneId(raw)
        except ValueError:
            return ZoneId(circuit.value)

    def mode(self, circuit: CircuitId) -> HeatingMode:
        raw = self._config.get(f"heating.circuits.{circuit.value}.mode", "manuel")
        return HeatingMode.AUTO if raw == "auto" else HeatingMode.MANUEL

    def thresholds(self, circuit: CircuitId) -> tuple[float | None, float | None]:
        return (
            self._config.get(f"heating.circuits.{circuit.value}.open_below_c"),
            self._config.get(f"heating.circuits.{circuit.value}.close_above_c"),
        )

    def fallback(self, circuit: CircuitId) -> SensorLossFallback:
        raw = self._config.get(f"heating.circuits.{circuit.value}.on_sensor_loss", "hold")
        try:
            return SensorLossFallback(raw)
        except ValueError:
            return SensorLossFallback.HOLD

    def thresholds_defined(self, circuit: CircuitId) -> bool:
        low, high = self.thresholds(circuit)
        return low is not None and high is not None

    # -- écriture -------------------------------------------------------
    def set_mode(self, circuit: CircuitId, mode: HeatingMode) -> bool:
        """Refuse AUTO tant que les deux seuils ne sont pas définis."""
        if mode is HeatingMode.AUTO and not self.thresholds_defined(circuit):
            return False
        self._config.set(f"heating.circuits.{circuit.value}.mode", mode.value)
        return True

    def set_thresholds(self, circuit: CircuitId,
                       open_below_c: float, close_above_c: float) -> None:
        """Applique deux seuils cohérents. Lève ``ValueError`` sinon."""
        delta = float(self._config.get("heating.min_threshold_delta_c", 1.0))
        if close_above_c < open_below_c + delta:
            raise ValueError(
                f"La fermeture doit dépasser l'ouverture d'au moins {delta:g} °C."
            )
        self._config.update({
            f"heating.circuits.{circuit.value}.open_below_c": float(open_below_c),
            f"heating.circuits.{circuit.value}.close_above_c": float(close_above_c),
        })

    def set_fallback(self, circuit: CircuitId, fallback: SensorLossFallback) -> None:
        """Réglage de sécurité : l'appelant doit avoir obtenu confirmation."""
        previous = self.fallback(circuit)
        self._config.set(f"heating.circuits.{circuit.value}.on_sensor_loss",
                         fallback.value)
        logger.info("repli sur perte de sonde — %s : %s → %s",
                    self.label(circuit), previous.value, fallback.value)

    # -- assemblage de l'état -------------------------------------------
    def build(
        self,
        temperatures: dict[ZoneId, TemperatureReading],
        valves: dict[CircuitId, Sample],
    ) -> dict[CircuitId, CircuitStatus]:
        statuses: dict[CircuitId, CircuitStatus] = {}
        for circuit in CIRCUIT_ORDER:
            statuses[circuit] = self._build_one(circuit, temperatures, valves.get(circuit))
        return statuses

    def _build_one(
        self,
        circuit: CircuitId,
        temperatures: dict[ZoneId, TemperatureReading],
        sample: Sample | None,
    ) -> CircuitStatus:
        zone = self.zone(circuit)
        temperature = temperatures.get(zone)
        low, high = self.thresholds(circuit)

        observation: ValveObservation | None = sample.value if sample else None
        if observation is None:
            reason = (sample.reason if sample else None) or "actionneur non intégré"
            return CircuitStatus(
                circuit=circuit, label=self.label(circuit), mode=self.mode(circuit),
                zone=zone,
                temperature_c=temperature.celsius if temperature else None,
                commanded=ValveCommand.NONE, confirmed=ConfirmedState.INCONNU,
                feedback_available=False, display_state=ValveState.INCONNU,
                state_is_certain=False,
                open_below_c=low, close_above_c=high,
                thresholds_defined=self.thresholds_defined(circuit),
                on_sensor_loss=self.fallback(circuit),
                fault=False, fault_reason=reason,
            )

        return CircuitStatus(
            circuit=circuit, label=self.label(circuit), mode=self.mode(circuit),
            zone=zone,
            temperature_c=temperature.celsius if temperature else None,
            commanded=observation.commanded,
            confirmed=observation.confirmed,
            feedback_available=observation.feedback_available,
            display_state=observation.display_state,
            state_is_certain=observation.state_is_certain,
            commanded_since=observation.updated_at,
            open_below_c=low, close_above_c=high,
            thresholds_defined=self.thresholds_defined(circuit),
            on_sensor_loss=self.fallback(circuit),
            fault=observation.fault,
            fault_reason=observation.reason,
        )


# ---------------------------------------------------------------------------
# Assemblage complet
# ---------------------------------------------------------------------------

class SnapshotBuilder:
    """Assemble l'instantané complet publié vers l'écran."""

    def __init__(self, config: ConfigStore, *, simulation: bool = False) -> None:
        self.temperatures = TemperatureService(config)
        self.tanks = TankService(config, simulation=simulation)
        self.battery = BatteryService(config)
        self.heating = HeatingService(config)

    def build(self, acquisition: AcquisitionSnapshot):
        from ..models import SystemSnapshot

        temperatures = self.temperatures.build(acquisition.temperatures)
        return SystemSnapshot(
            timestamp=acquisition.timestamp,
            temperatures=temperatures,
            tanks=self.tanks.build(acquisition.levels),
            battery=self.battery.build(acquisition.battery),
            circuits=self.heating.build(temperatures, acquisition.valves),
            alerts=(),
            simulation=acquisition.simulation,
        )
