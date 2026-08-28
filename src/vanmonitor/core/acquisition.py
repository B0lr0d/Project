"""Assemblage de la couche d'acquisition : un thread par famille de matériel.

Ce module crée les emplacements de valeurs (``LatestValue``), les threads qui
les remplissent, et rend un instantané cohérent de tout ce qui a été lu.

Il ne fait aucune interprétation métier : pas de calibration, pas
d'hystérésis, pas d'alerte. Une température y est un nombre de degrés brut, un
niveau y est une valeur sans unité. Les étapes 4 à 8 poseront le sens par
dessus, sans toucher à cette couche.

(Fichier ajouté par rapport à l'arborescence de l'étape 1 : l'assemblage
n'avait pas de place attitrée, et le loger dans ``workers.py`` aurait mélangé
le mécanisme générique des threads avec la description de l'installation.)
"""

from __future__ import annotations

from typing import Callable

from ..config import ConfigStore
from ..constants import CIRCUIT_ORDER, CircuitId, TANK_ORDER, TankId, ZONE_ORDER, ZoneId
from ..hal.factory import HalBundle
from ..hal.interfaces import HardwareError
from ..models import AcquisitionSnapshot, Sample, WorkerHealth
from ..util.logging_setup import get_logger
from ..util.ratelimit import RateLimitedLogger
from ..util.timebase import monotonic
from .commands import CommandBus
from .state import LatestValue
from .workers import HardwareWorker, ValveWorker, WorkerSupervisor

logger = get_logger("core.acquisition")
limited = RateLimitedLogger(logger)


class AcquisitionService:
    """Les threads de matériel, leurs valeurs, et l'instantané qui en découle."""

    def __init__(
        self,
        hal: HalBundle,
        config: ConfigStore,
        command_bus: CommandBus,
    ) -> None:
        self._hal = hal
        self._config = config
        self._bus = command_bus

        self._temperature_slots = {
            zone: LatestValue(f"temperature.{zone.value}") for zone in ZONE_ORDER
        }
        self._level_slots = {
            tank: LatestValue(f"level.{tank.value}") for tank in TANK_ORDER
        }
        self._battery_slot = LatestValue("battery")
        self._valve_slots = {
            circuit: LatestValue(f"valve.{circuit.value}") for circuit in CIRCUIT_ORDER
        }

        self._supervisor = WorkerSupervisor(
            backoff_s=config.get("workers.restart_backoff_s", [5, 15, 60, 300]),
        )
        self._valve_worker: ValveWorker | None = None
        self._battery_retry_at = 0.0
        self._battery_backoff_index = 0
        self._started = False

        self._mark_unconfigured_equipment()

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._started:
            return
        watchdog = float(self._config.get("workers.watchdog_factor", 3))

        self._supervisor.register(lambda: HardwareWorker(
            "temp_worker",
            self._task_temperatures,
            period_s=lambda: float(self._config.get("temperatures.poll_period_s", 10)),
            deadline_s=watchdog * max(
                float(self._config.get("temperatures.poll_period_s", 10)),
                float(self._config.get("temperatures.read_timeout_s", 3.0)),
            ),
        ))
        self._supervisor.register(lambda: HardwareWorker(
            "level_worker",
            self._task_levels,
            period_s=lambda: float(self._config.get("tanks.poll_period_s", 2)),
            deadline_s=watchdog * max(
                float(self._config.get("tanks.poll_period_s", 2)),
                float(self._config.get("tanks.read_timeout_s", 1.0)),
            ),
        ))
        self._supervisor.register(lambda: HardwareWorker(
            "battery_worker",
            self._task_battery,
            period_s=lambda: float(self._config.get("battery.poll_period_s", 1.0)),
            deadline_s=watchdog * max(
                float(self._config.get("battery.poll_period_s", 1.0)),
                float(self._config.get("battery.read_timeout_s", 2.0)),
            ),
        ))
        self._supervisor.start_all()

        if self._hal.valves:
            self._valve_worker = ValveWorker(
                self._hal.valves, self._bus, self._valve_slots,
            )
            self._valve_worker.start()

        self._started = True
        logger.info("acquisition démarrée (%s)",
                    "simulation" if self._hal.simulation else "matériel réel")

    def stop(self, timeout_s: float = 3.0) -> None:
        if not self._started:
            return
        if self._valve_worker is not None:
            self._valve_worker.request_stop()
            self._valve_worker.join(timeout=timeout_s)
        self._supervisor.stop_all(timeout_s=timeout_s)
        if self._hal.smartshunt is not None:
            try:
                self._hal.smartshunt.disconnect()
            except Exception:
                pass
        self._started = False
        logger.info("acquisition arrêtée")

    # ------------------------------------------------------------------
    # Instantané
    # ------------------------------------------------------------------
    def check_workers(self) -> list[WorkerHealth]:
        """Relève la santé des threads et remplace ceux qui sont bloqués."""
        return self._supervisor.check()

    def snapshot(self, worker_health: list[WorkerHealth] | None = None) -> AcquisitionSnapshot:
        """Lecture immédiate de tout ce qui est connu. Ne touche aucun matériel."""
        now = monotonic()
        temp_stale = float(self._config.get("temperatures.stale_after_s", 60))
        tank_stale = float(self._config.get("tanks.stale_after_s", 30))
        battery_stale = float(self._config.get("battery.stale_after_s", 15))

        return AcquisitionSnapshot(
            timestamp=now,
            temperatures={
                zone: slot.get(now, temp_stale)
                for zone, slot in self._temperature_slots.items()
            },
            levels={
                tank: slot.get(now, tank_stale)
                for tank, slot in self._level_slots.items()
            },
            battery=self._battery_slot.get(now, battery_stale),
            valves={
                circuit: slot.get(now)
                for circuit, slot in self._valve_slots.items()
            },
            workers=tuple(worker_health if worker_health is not None
                          else self._supervisor.health(now)),
            simulation=self._hal.simulation,
        )

    def raw_level(self, tank: TankId) -> float | None:
        """Dernière valeur brute d'un réservoir (utilisée par la calibration)."""
        sample = self._level_slots[tank].get()
        return sample.value if sample.ok else None

    # ------------------------------------------------------------------
    # Tâches d'acquisition
    # ------------------------------------------------------------------
    def _mark_unconfigured_equipment(self) -> None:
        """Une zone sans sonde associée est un état normal, pas une panne."""
        for zone in ZONE_ORDER:
            if self._hal.temperature_sensors.get(zone) is None:
                self._temperature_slots[zone].mark_absent("sonde non associée")
        for tank in TANK_ORDER:
            if self._hal.level_sensors.get(tank) is None:
                self._level_slots[tank].mark_absent("capteur non intégré")
        if self._hal.smartshunt is None:
            self._battery_slot.mark_absent("liaison non intégrée")
        for circuit in CIRCUIT_ORDER:
            if circuit not in self._hal.valves:
                self._valve_slots[circuit].mark_absent("actionneur non intégré")

    def _task_temperatures(self) -> None:
        """Lit les cinq sondes, une par une, sans qu'une panne en cache une autre."""
        low, high = self._config.get("temperatures.valid_range_c", [-40.0, 85.0])
        for zone in ZONE_ORDER:
            sensor = self._hal.temperature_sensors.get(zone)
            slot = self._temperature_slots[zone]
            if sensor is None:
                continue        # déjà marquée absente au démarrage
            key = f"temp.{zone.value}"
            # Instant du début de la mesure : c'est lui qui date la valeur, et
            # qui permet d'écarter la publication d'un thread abandonné.
            started = monotonic()
            if not sensor.is_present():
                # Sonde disparue du bus : état normal (`--`), pas une panne.
                slot.mark_absent("sonde non détectée sur le bus", started)
                limited.warning(key, f"sonde {zone.value} non détectée sur le bus")
                continue
            try:
                celsius = sensor.read_celsius()
            except HardwareError as exc:
                slot.mark_fault(str(exc), started)
                limited.warning(key, f"température {zone.value} : {exc}")
                continue
            except Exception as exc:
                slot.mark_fault(f"{type(exc).__name__}: {exc}", started)
                limited.error(key, f"température {zone.value} : erreur inattendue ({exc})")
                continue

            if not (float(low) <= celsius <= float(high)):
                slot.mark_fault(f"valeur hors plage ({celsius:.1f} °C)", started)
                limited.warning(key, f"température {zone.value} hors plage : {celsius:.1f} °C")
                continue

            slot.set(celsius, started)
            limited.clear(key)

    def _task_levels(self) -> None:
        for tank in TANK_ORDER:
            sensor = self._hal.level_sensors.get(tank)
            slot = self._level_slots[tank]
            if sensor is None:
                continue
            key = f"level.{tank.value}"
            started = monotonic()
            if not sensor.is_present():
                slot.mark_absent("capteur non détecté", started)
                limited.warning(key, f"capteur de niveau {tank.value} non détecté")
                continue
            try:
                raw = sensor.read_raw()
            except HardwareError as exc:
                slot.mark_fault(str(exc), started)
                limited.warning(key, f"niveau {tank.value} : {exc}")
                continue
            except Exception as exc:
                slot.mark_fault(f"{type(exc).__name__}: {exc}", started)
                limited.error(key, f"niveau {tank.value} : erreur inattendue ({exc})")
                continue
            slot.set(raw, started)
            limited.clear(key)

    def _task_battery(self) -> None:
        """Lit le SmartShunt, en gérant la reconnexion sans jamais boucler."""
        shunt = self._hal.smartshunt
        if shunt is None:
            return

        now = monotonic()
        if not shunt.is_connected():
            if now < self._battery_retry_at:
                return          # temporisation en cours : on ne harcèle pas la liaison
            try:
                shunt.connect()
            except HardwareError as exc:
                self._battery_slot.mark_fault(str(exc), now)
                self._schedule_battery_retry(now)
                limited.warning("battery.connect", f"SmartShunt injoignable : {exc}")
                return
            self._battery_backoff_index = 0
            limited.clear("battery.connect")
            logger.info("SmartShunt : liaison ouverte")

        started = monotonic()
        try:
            reading = shunt.read()
        except HardwareError as exc:
            self._battery_slot.mark_fault(str(exc), started)
            self._schedule_battery_retry(monotonic())
            limited.warning("battery.read", f"SmartShunt : {exc}")
            return
        except Exception as exc:
            self._battery_slot.mark_fault(f"{type(exc).__name__}: {exc}", started)
            self._schedule_battery_retry(monotonic())
            limited.error("battery.read", f"SmartShunt : erreur inattendue ({exc})")
            return

        self._battery_slot.set(reading, started)
        self._battery_backoff_index = 0
        limited.clear("battery.read")

    def _schedule_battery_retry(self, now: float) -> None:
        backoff = self._config.get("battery.reconnect_backoff_s", [1, 2, 5, 10, 30])
        index = min(self._battery_backoff_index, len(backoff) - 1)
        self._battery_retry_at = now + float(backoff[index])
        self._battery_backoff_index = min(self._battery_backoff_index + 1, len(backoff) - 1)
