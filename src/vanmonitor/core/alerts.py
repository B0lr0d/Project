"""Moteur d'alertes.

Peu d'alertes, et aucune quand tout va bien : c'est la règle de départ. Une
alerte qui apparaît doit vouloir dire quelque chose, sinon on cesse de les lire.

Deux garde-fous contre le bavardage :

* **durée minimale** — une valeur qui frôle le seuil une seconde n'alerte pas ;
* **marge de réarmement** — une alerte ne s'éteint qu'après être repassée
  nettement du bon côté du seuil, sinon elle clignoterait autour de la limite.

Étape 8 : réglage fin des marges et jeu de tests dédié. Les règles sont ici.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config import ConfigStore
from ..constants import (
    AlertLevel,
    CIRCUIT_ORDER,
    CircuitId,
    Status,
    TANK_ORDER,
    TankId,
    ZONE_ORDER,
    ZoneId,
)
from ..models import Alert, BatteryReading, CircuitStatus, TankReading, TemperatureReading
from ..util.timebase import monotonic


@dataclass
class _Pending:
    """Suivi d'une condition : depuis quand elle est vraie, depuis quand active."""

    since: float | None = None
    active_since: float | None = None


class AlertEngine:
    """Évalue les alertes à partir de l'instantané, sans effet de bord."""

    def __init__(self, config: ConfigStore) -> None:
        self._config = config
        self._state: dict[str, _Pending] = {}

    # ------------------------------------------------------------------
    def evaluate(
        self,
        *,
        battery: BatteryReading,
        tanks: dict[TankId, TankReading],
        temperatures: dict[ZoneId, TemperatureReading],
        circuits: dict[CircuitId, CircuitStatus],
        worker_health: tuple = (),
        now: float | None = None,
    ) -> tuple[Alert, ...]:
        instant = monotonic() if now is None else now
        margin = float(self._config.get("alerts.rearm_margin_pct", 3))
        alerts: list[Alert] = []
        seen: set[str] = set()

        def consider(key: str, active: bool, level: AlertLevel, message: str,
                     *, immediate: bool = False) -> None:
            seen.add(key)
            alert = self._track(key, active, level, message, instant,
                                immediate=immediate)
            if alert is not None:
                alerts.append(alert)

        # --- niveaux et batterie ---------------------------------------
        soc_min = float(self._config.get("alerts.battery_soc_min_pct", 20))
        consider(
            "batterie_basse",
            self._below(battery.soc_percent, soc_min, margin, "batterie_basse"),
            AlertLevel.CRITIQUE,
            f"Batterie {_pct(battery.soc_percent)}",
        )

        thresholds = {
            TankId.EAU_PROPRE: ("alerts.fresh_water_min_pct", 20.0, False),
            TankId.GASOIL: ("alerts.fuel_min_pct", 20.0, False),
            TankId.EAUX_GRISES: ("alerts.grey_water_max_pct", 80.0, True),
        }
        for tank in TANK_ORDER:
            key_path, default, above = thresholds[tank]
            limit = float(self._config.get(key_path, default))
            reading = tanks.get(tank)
            percent = reading.percent if reading else None
            key = f"{tank.value}_{'haut' if above else 'bas'}"
            active = (self._above(percent, limit, margin, key) if above
                      else self._below(percent, limit, margin, key))
            consider(
                key, active,
                AlertLevel.WARN,
                f"{reading.label if reading else tank.value} {_pct(percent)}",
            )

        # --- alertes techniques ----------------------------------------
        if bool(self._config.get("alerts.technical_alerts", True)):
            for zone in ZONE_ORDER:
                critical = bool(
                    self._config.get(f"temperatures.zones.{zone.value}.critical", False)
                )
                reading = temperatures.get(zone)
                if not critical or reading is None:
                    continue
                consider(
                    f"sonde_{zone.value}",
                    reading.status in (Status.FAULT, Status.ABSENT, Status.STALE),
                    AlertLevel.WARN,
                    f"Sonde {reading.label} : {_status_text(reading.status)}",
                )

            consider(
                "smartshunt",
                battery.status in (Status.FAULT, Status.STALE),
                AlertLevel.WARN,
                "SmartShunt non joignable",
            )

            for circuit in CIRCUIT_ORDER:
                status = circuits.get(circuit)
                if status is None:
                    continue
                consider(
                    f"circuit_{circuit.value}",
                    status.fault,
                    AlertLevel.WARN,
                    f"Défaut chauffage {status.label}",
                )
                consider(
                    f"repli_{circuit.value}",
                    status.fallback_active,
                    AlertLevel.WARN,
                    f"Repli actif — {status.label}",
                )

            stuck = [health.name for health in worker_health if health.stuck]
            consider(
                "acquisition_bloquee",
                bool(stuck),
                AlertLevel.WARN,
                "Acquisition bloquée : " + ", ".join(stuck) if stuck else "",
                immediate=True,
            )

        for key in list(self._state):
            if key not in seen:
                del self._state[key]

        return tuple(sorted(alerts, key=lambda alert: (
            0 if alert.level is AlertLevel.CRITIQUE else 1, alert.active_since,
        )))

    # ------------------------------------------------------------------
    def _track(self, key: str, active: bool, level: AlertLevel, message: str,
               now: float, *, immediate: bool) -> Alert | None:
        pending = self._state.setdefault(key, _Pending())

        if not active:
            pending.since = None
            pending.active_since = None
            return None

        if pending.since is None:
            pending.since = now

        minimum = 0.0 if immediate else float(
            self._config.get("alerts.min_duration_s", 30)
        )
        if pending.active_since is None:
            if now - pending.since < minimum:
                return None
            pending.active_since = now

        return Alert(key=key, level=level, message=message,
                     active_since=pending.active_since)

    def _below(self, value: float | None, limit: float, margin: float, key: str) -> bool:
        if value is None:
            return False
        already = self._state.get(key, _Pending()).active_since is not None
        return value < (limit + margin if already else limit)

    def _above(self, value: float | None, limit: float, margin: float, key: str) -> bool:
        if value is None:
            return False
        already = self._state.get(key, _Pending()).active_since is not None
        return value > (limit - margin if already else limit)


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:.0f} %"


def _status_text(status: Status) -> str:
    return {
        Status.FAULT: "erreur capteur",
        Status.ABSENT: "absente",
        Status.STALE: "ne répond plus",
    }.get(status, "")
