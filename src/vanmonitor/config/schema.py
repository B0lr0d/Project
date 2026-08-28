"""Validation et migration de la configuration.

Principe directeur : **une configuration abîmée ne doit jamais empêcher le
démarrage**. Toute valeur invalide est remplacée par la valeur par défaut et
signalée dans une liste d'avertissements, que l'appelant journalise.

La validation reste volontairement simple : pas de bibliothèque de schéma,
juste des vérifications de type et de plage sur les clés qui comptent.
"""

from __future__ import annotations

import copy
from typing import Any

from ..constants import CircuitId, SensorLossFallback, TankId, ZoneId
from .defaults import CONFIG_VERSION, default_config

_VALID_MODES = {"auto", "manuel"}
_VALID_UNITS = {"litres", "percent"}
_VALID_FALLBACKS = {item.value for item in SensorLossFallback}
_VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def deep_merge(base: dict[str, Any], overlay: Any) -> dict[str, Any]:
    """Fusionne ``overlay`` dans une copie de ``base``.

    Seules les clés déjà connues de ``base`` sont reprises : une clé inconnue
    dans le fichier (relique d'une ancienne version, faute de frappe) est
    ignorée plutôt que propagée.
    """
    result = copy.deepcopy(base)
    if not isinstance(overlay, dict):
        return result

    for key, value in overlay.items():
        if key not in result:
            continue
        if isinstance(result[key], dict):
            # Une section attendue mais remplacée par autre chose (fichier
            # tronqué, édition manuelle malheureuse) est ignorée : mieux vaut
            # la section par défaut qu'une chaîne à la place d'un dictionnaire.
            if isinstance(value, dict):
                result[key] = deep_merge(result[key], value)
            continue
        if isinstance(result[key], list) and not isinstance(value, list):
            continue
        result[key] = copy.deepcopy(value)
    return result


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fix(
    section: dict[str, Any],
    key: str,
    defaults: dict[str, Any],
    warnings: list[str],
    path: str,
    reason: str,
) -> None:
    section[key] = copy.deepcopy(defaults[key])
    warnings.append(f"{path}: {reason} — valeur par défaut rétablie ({section[key]!r})")


def _check_number(
    section: dict[str, Any],
    key: str,
    defaults: dict[str, Any],
    warnings: list[str],
    path: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    allow_none: bool = False,
) -> None:
    value = section.get(key)
    if value is None and allow_none:
        return
    number = _as_number(value)
    if number is None:
        _fix(section, key, defaults, warnings, f"{path}.{key}", "valeur non numérique")
        return
    if minimum is not None and number < minimum:
        _fix(section, key, defaults, warnings, f"{path}.{key}", f"inférieure à {minimum}")
        return
    if maximum is not None and number > maximum:
        _fix(section, key, defaults, warnings, f"{path}.{key}", f"supérieure à {maximum}")


def _check_bool(
    section: dict[str, Any],
    key: str,
    defaults: dict[str, Any],
    warnings: list[str],
    path: str,
) -> None:
    if not isinstance(section.get(key), bool):
        _fix(section, key, defaults, warnings, f"{path}.{key}", "valeur non booléenne")


def _check_choice(
    section: dict[str, Any],
    key: str,
    defaults: dict[str, Any],
    warnings: list[str],
    path: str,
    choices: set[str],
) -> None:
    if section.get(key) not in choices:
        _fix(
            section, key, defaults, warnings, f"{path}.{key}",
            f"valeur hors de {sorted(choices)}",
        )


def validate(raw: Any) -> tuple[dict[str, Any], list[str]]:
    """Retourne ``(configuration saine, avertissements)``.

    ``raw`` peut être n'importe quoi : ``None``, un fichier tronqué décodé en
    liste, un dictionnaire partiel. Le résultat est toujours utilisable.
    """
    warnings: list[str] = []
    defaults = default_config()

    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append("fichier de configuration illisible — valeurs par défaut utilisées")
        return defaults, warnings

    version = raw.get("config_version")
    if version is not None and version != CONFIG_VERSION:
        warnings.append(
            f"config_version {version!r} inattendue (attendu {CONFIG_VERSION}) — "
            "fusion avec les valeurs par défaut"
        )

    config = deep_merge(defaults, raw)
    config["config_version"] = CONFIG_VERSION

    # --- général -------------------------------------------------------
    general, general_defaults = config["general"], defaults["general"]
    _check_bool(general, "simulation", general_defaults, warnings, "general")
    _check_bool(general, "fullscreen", general_defaults, warnings, "general")
    _check_number(general, "ui_refresh_hz", general_defaults, warnings, "general",
                  minimum=0.2, maximum=30)

    # --- threads d'acquisition -----------------------------------------
    workers, workers_defaults = config["workers"], defaults["workers"]
    _check_number(workers, "watchdog_factor", workers_defaults, warnings, "workers",
                  minimum=1.5, maximum=100)
    backoff = workers.get("restart_backoff_s")
    if (not isinstance(backoff, list) or not backoff
            or any(_as_number(item) is None or _as_number(item) < 0 for item in backoff)):
        _fix(workers, "restart_backoff_s", workers_defaults, warnings,
             "workers.restart_backoff_s", "liste de délais invalide")

    # --- températures ---------------------------------------------------
    temps, temps_defaults = config["temperatures"], defaults["temperatures"]
    _check_number(temps, "poll_period_s", temps_defaults, warnings, "temperatures",
                  minimum=1, maximum=3600)
    _check_number(temps, "read_timeout_s", temps_defaults, warnings, "temperatures",
                  minimum=0.1, maximum=60)
    _check_number(temps, "stale_after_s", temps_defaults, warnings, "temperatures",
                  minimum=1, maximum=86400)

    valid_range = temps.get("valid_range_c")
    if (not isinstance(valid_range, list) or len(valid_range) != 2
            or _as_number(valid_range[0]) is None or _as_number(valid_range[1]) is None
            or float(valid_range[0]) >= float(valid_range[1])):
        _fix(temps, "valid_range_c", temps_defaults, warnings,
             "temperatures.valid_range_c", "plage invalide")

    known_zones = {zone.value for zone in ZoneId}
    for name in list(temps["zones"]):
        if name not in known_zones:
            del temps["zones"][name]
            warnings.append(f"temperatures.zones.{name}: zone inconnue — ignorée")
    for name, zone in temps["zones"].items():
        zone_defaults = temps_defaults["zones"][name]
        if not isinstance(zone.get("label"), str) or not zone["label"].strip():
            _fix(zone, "label", zone_defaults, warnings,
                 f"temperatures.zones.{name}", "libellé vide")
        sensor_id = zone.get("sensor_id")
        if sensor_id is not None and not isinstance(sensor_id, str):
            _fix(zone, "sensor_id", zone_defaults, warnings,
                 f"temperatures.zones.{name}", "identifiant de sonde invalide")
        _check_number(zone, "offset_c", zone_defaults, warnings,
                      f"temperatures.zones.{name}", minimum=-20, maximum=20)
        _check_bool(zone, "critical", zone_defaults, warnings, f"temperatures.zones.{name}")

    # une même sonde ne peut pas servir deux zones
    seen: dict[str, str] = {}
    for name, zone in temps["zones"].items():
        sensor_id = zone.get("sensor_id")
        if not sensor_id:
            continue
        if sensor_id in seen:
            zone["sensor_id"] = None
            warnings.append(
                f"temperatures.zones.{name}: sonde {sensor_id} déjà associée à "
                f"« {seen[sensor_id]} » — association retirée"
            )
        else:
            seen[sensor_id] = name

    # --- réservoirs ------------------------------------------------------
    tanks, tanks_defaults = config["tanks"], defaults["tanks"]
    _check_number(tanks, "poll_period_s", tanks_defaults, warnings, "tanks",
                  minimum=0.2, maximum=3600)
    _check_number(tanks, "read_timeout_s", tanks_defaults, warnings, "tanks",
                  minimum=0.05, maximum=60)
    _check_number(tanks, "stale_after_s", tanks_defaults, warnings, "tanks",
                  minimum=1, maximum=86400)
    for tank_id in TankId:
        name = tank_id.value
        tank, tank_defaults = tanks[name], tanks_defaults[name]
        _check_choice(tank, "unit", tank_defaults, warnings, f"tanks.{name}", _VALID_UNITS)
        _check_number(tank, "capacity_l", tank_defaults, warnings, f"tanks.{name}",
                      minimum=0.1, maximum=10000, allow_none=True)
        if not isinstance(tank.get("calibration"), dict):
            _fix(tank, "calibration", tank_defaults, warnings,
                 f"tanks.{name}", "table de calibration invalide")
        elif not isinstance(tank["calibration"].get("points"), list):
            tank["calibration"]["points"] = []
            warnings.append(f"tanks.{name}.calibration.points: liste invalide — table vidée")

    # --- batterie --------------------------------------------------------
    battery, battery_defaults = config["battery"], defaults["battery"]
    _check_number(battery, "poll_period_s", battery_defaults, warnings, "battery",
                  minimum=0.2, maximum=60)
    _check_number(battery, "read_timeout_s", battery_defaults, warnings, "battery",
                  minimum=0.1, maximum=60)
    _check_number(battery, "stale_after_s", battery_defaults, warnings, "battery",
                  minimum=1, maximum=3600)
    _check_bool(battery, "show_time_to_go", battery_defaults, warnings, "battery")

    # --- chauffage -------------------------------------------------------
    heating, heating_defaults = config["heating"], defaults["heating"]
    _check_number(heating, "control_period_s", heating_defaults, warnings, "heating",
                  minimum=1, maximum=600)
    _check_number(heating, "min_state_dwell_s", heating_defaults, warnings, "heating",
                  minimum=0, maximum=3600)
    _check_number(heating, "transition_timeout_s", heating_defaults, warnings, "heating",
                  minimum=1, maximum=3600)
    _check_number(heating, "min_threshold_delta_c", heating_defaults, warnings, "heating",
                  minimum=0.1, maximum=20)
    _check_number(heating, "command_timeout_s", heating_defaults, warnings, "heating",
                  minimum=0.1, maximum=60)

    known_circuits = {circuit.value for circuit in CircuitId}
    for name in list(heating["circuits"]):
        if name not in known_circuits:
            del heating["circuits"][name]
            warnings.append(f"heating.circuits.{name}: circuit inconnu — ignoré")

    delta = float(heating["min_threshold_delta_c"])
    for name, circuit in heating["circuits"].items():
        circuit_defaults = heating_defaults["circuits"][name]
        path = f"heating.circuits.{name}"

        if not isinstance(circuit.get("label"), str) or not circuit["label"].strip():
            _fix(circuit, "label", circuit_defaults, warnings, path, "libellé vide")
        if circuit.get("zone") not in known_zones:
            _fix(circuit, "zone", circuit_defaults, warnings, path, "zone inconnue")
        _check_choice(circuit, "mode", circuit_defaults, warnings, path, _VALID_MODES)
        _check_choice(circuit, "on_sensor_loss", circuit_defaults, warnings, path,
                      _VALID_FALLBACKS)
        _check_number(circuit, "open_below_c", circuit_defaults, warnings, path,
                      minimum=-40, maximum=85, allow_none=True)
        _check_number(circuit, "close_above_c", circuit_defaults, warnings, path,
                      minimum=-40, maximum=85, allow_none=True)

        low, high = circuit.get("open_below_c"), circuit.get("close_above_c")
        if low is not None and high is not None and float(high) < float(low) + delta:
            circuit["open_below_c"] = None
            circuit["close_above_c"] = None
            warnings.append(
                f"{path}: seuils incohérents (fermeture < ouverture + {delta} °C) — "
                "seuils effacés, mode AUTO indisponible"
            )

        # Un circuit sans seuils ne peut pas tourner en AUTO.
        if (circuit.get("open_below_c") is None or circuit.get("close_above_c") is None) \
                and circuit.get("mode") == "auto":
            circuit["mode"] = "manuel"
            warnings.append(f"{path}: seuils non définis — mode ramené à MANUEL")

    # --- alertes ---------------------------------------------------------
    alerts, alerts_defaults = config["alerts"], defaults["alerts"]
    for key in ("battery_soc_min_pct", "fresh_water_min_pct",
                "fuel_min_pct", "grey_water_max_pct"):
        _check_number(alerts, key, alerts_defaults, warnings, "alerts",
                      minimum=0, maximum=100)
    _check_number(alerts, "rearm_margin_pct", alerts_defaults, warnings, "alerts",
                  minimum=0, maximum=50)
    _check_number(alerts, "min_duration_s", alerts_defaults, warnings, "alerts",
                  minimum=0, maximum=3600)
    _check_bool(alerts, "technical_alerts", alerts_defaults, warnings, "alerts")

    # --- historique ------------------------------------------------------
    history, history_defaults = config["history"], defaults["history"]
    _check_bool(history, "enabled", history_defaults, warnings, "history")
    _check_number(history, "sample_period_s", history_defaults, warnings, "history",
                  minimum=10, maximum=86400)
    _check_number(history, "retention_hours", history_defaults, warnings, "history",
                  minimum=1, maximum=720)
    _check_number(history, "batch_size", history_defaults, warnings, "history",
                  minimum=1, maximum=1000)

    # --- journalisation --------------------------------------------------
    logging_section, logging_defaults = config["logging"], defaults["logging"]
    if str(logging_section.get("level", "")).upper() not in _VALID_LOG_LEVELS:
        _fix(logging_section, "level", logging_defaults, warnings, "logging",
             "niveau inconnu")
    else:
        logging_section["level"] = str(logging_section["level"]).upper()
    _check_number(logging_section, "dedup_window_s", logging_defaults, warnings, "logging",
                  minimum=0, maximum=86400)

    return config, warnings
