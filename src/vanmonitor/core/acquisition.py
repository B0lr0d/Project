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

import threading
from typing import Callable

from ..config import ConfigStore
from ..constants import (
    CIRCUIT_ORDER,
    CircuitId,
    Status,
    TANK_ORDER,
    TankId,
    ZONE_ORDER,
    ZoneId,
)
from ..hal.factory import HalBundle
from ..hal.interfaces import HardwareError
from ..models import AcquisitionSnapshot, Sample, WorkerHealth
from ..util.logging_setup import get_logger
from ..util.ratelimit import RateLimitedLogger
from ..util.timebase import monotonic
from .commands import CommandBus
from .filters import SpikeGuard
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

        # --- températures : bus vivant, filtrage, identification ---------
        max_step = float(config.get("temperatures.max_step_c", 12.0))
        self._temperature_guards = {zone: SpikeGuard(max_step) for zone in ZONE_ORDER}
        self._available_sensor_ids: tuple[str, ...] = ()
        self._sensor_temperatures: dict[str, Sample] = {}
        #: Lecture des sondes détectées mais non associées. Coûteuse sur un bus
        #: 1-Wire, donc réservée au moment où l'on cherche à identifier une
        #: sonde — c'est-à-dire quand la section Sondes est ouverte.
        self._identification = False
        self._rebind_lock = threading.Lock()

        self._mark_unconfigured_equipment()
        config.add_listener(self._on_config_changed)

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
            available_sensor_ids=self._available_sensor_ids,
            sensor_temperatures=dict(self._sensor_temperatures),
            simulation=self._hal.simulation,
        )

    # ------------------------------------------------------------------
    # Bus 1-Wire : association à chaud et identification
    # ------------------------------------------------------------------
    def set_identification_mode(self, enabled: bool) -> None:
        """Active la lecture des sondes non associées.

        Activé seulement quand la section Sondes est ouverte : sur un bus
        1-Wire, chaque sonde lue en plus coûte près d'une seconde par cycle.
        """
        self._identification = bool(enabled)
        if not enabled:
            self._sensor_temperatures = {}

    def _on_config_changed(self, path: str) -> None:
        """Réassocie une sonde dès que l'utilisateur change son affectation.

        Sans cela, un changement d'association ne prendrait effet qu'au
        prochain démarrage — ce qui rendrait la page Sondes trompeuse.
        """
        if not path.startswith("temperatures.zones."):
            return
        parts = path.split(".")
        if len(parts) < 4 or parts[3] != "sensor_id":
            return
        try:
            zone = ZoneId(parts[2])
        except ValueError:
            return

        with self._rebind_lock:
            sensor = self._hal.rebuild_temperature_sensor(self._config, zone)
            self._temperature_guards[zone].reset()
            slot = self._temperature_slots[zone]
            # La valeur de l'ancienne sonde n'a plus rien à voir avec la zone :
            # on l'écarte, et on refuse toute lecture entamée avant ce point.
            slot.mark_absent(
                "sonde non associée" if sensor is None else "réassociation en cours",
                monotonic(),
            )
        logger.info("zone %s : sonde réassociée (%s)", zone.value,
                    self._config.get(f"temperatures.zones.{zone.value}.sensor_id")
                    or "aucune")

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
        """Un tour complet du bus 1-Wire.

        Trois choses, dans cet ordre : recenser les sondes réellement présentes,
        lire celle de chaque zone associée, et — seulement si l'on cherche à
        identifier une sonde — lire aussi celles qui ne sont associées à rien.

        Aucune panne n'en cache une autre : chaque sonde est lue dans son propre
        ``try``, et une sonde muette n'empêche pas les quatre autres de
        répondre.
        """
        self._available_sensor_ids = tuple(self._hal.scan_temperature_sensor_ids())
        readings: dict[str, Sample] = {}

        low, high = self._config.get("temperatures.valid_range_c", [-40.0, 85.0])
        for zone in ZONE_ORDER:
            sensor = self._hal.temperature_sensors.get(zone)
            if sensor is None:
                continue        # zone non associée : déjà marquée absente
            sample = self._read_zone(zone, sensor, float(low), float(high))
            if sample is not None and sensor is not None:
                try:
                    readings[sensor.sensor_id()] = sample
                except Exception:       # un pilote qui refuse de se nommer
                    pass

        if self._identification:
            readings.update(self._read_unbound_sensors(float(low), float(high)))

        self._sensor_temperatures = readings

    def _read_zone(self, zone: ZoneId, sensor, low: float, high: float) -> Sample | None:
        """Lit une zone et publie le résultat. Rend l'échantillon obtenu."""
        slot = self._temperature_slots[zone]
        key = f"temp.{zone.value}"
        guard = self._temperature_guards[zone]

        # Instant du début de la mesure : c'est lui qui date la valeur, et qui
        # permet d'écarter la publication d'un thread abandonné.
        started = monotonic()

        if not sensor.is_present():
            # Sonde disparue du bus : état normal (`--`), pas une panne.
            guard.reset()
            slot.mark_absent("sonde non détectée sur le bus", started)
            limited.warning(key, f"sonde {zone.value} non détectée sur le bus")
            return slot.get(started)

        try:
            celsius = sensor.read_celsius()
        except HardwareError as exc:
            guard.reset()
            slot.mark_fault(str(exc), started)
            limited.warning(key, f"température {zone.value} : {exc}")
            return slot.get(started)
        except Exception as exc:
            guard.reset()
            slot.mark_fault(f"{type(exc).__name__}: {exc}", started)
            limited.error(key, f"température {zone.value} : erreur inattendue ({exc})")
            return slot.get(started)

        if not (low <= celsius <= high):
            guard.reset()
            slot.mark_fault(f"valeur hors plage ({celsius:.1f} °C)", started)
            limited.warning(key, f"température {zone.value} hors plage : {celsius:.1f} °C")
            return slot.get(started)

        accepted, reason = guard.accept(celsius)
        if not accepted:
            # Valeur isolée invraisemblable : on garde la précédente et on
            # attend confirmation. Le statut ne change pas — ce n'est pas une
            # panne, c'est une trame douteuse.
            limited.warning(f"{key}.spike", f"température {zone.value} : {reason}")
            return slot.get(started)

        slot.set(celsius, started)
        limited.clear(key)
        limited.clear(f"{key}.spike")
        return slot.get(started)

    def _read_unbound_sensors(self, low: float, high: float) -> dict[str, Sample]:
        """Lit les sondes détectées mais associées à aucune zone.

        C'est ce qui permet d'identifier physiquement une sonde avant de
        l'associer : on la réchauffe à la main et on regarde quelle ligne monte.
        Réservé au mode identification, car chaque lecture supplémentaire
        occupe le bus une seconde de plus.
        """
        bound: set[str] = set()
        for sensor in self._hal.temperature_sensors.values():
            if sensor is None:
                continue
            try:
                bound.add(sensor.sensor_id())
            except Exception:
                continue

        readings: dict[str, Sample] = {}
        for sensor_id in self._available_sensor_ids:
            if sensor_id in bound:
                continue
            sensor = self._hal.sensor_for_id(self._config, sensor_id)
            if sensor is None:
                continue
            started = monotonic()
            try:
                celsius = sensor.read_celsius()
            except Exception as exc:
                readings[sensor_id] = Sample(None, Status.FAULT, None, None, str(exc))
                continue
            status = Status.OK if low <= celsius <= high else Status.FAULT
            readings[sensor_id] = Sample(
                celsius if status is Status.OK else None,
                status, started, 0.0,
                None if status is Status.OK else "valeur hors plage",
            )
        return readings

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
