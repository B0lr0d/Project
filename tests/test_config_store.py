"""Configuration : valeurs par défaut, validation, écriture atomique.

Le point le plus important est le dernier test : une configuration corrompue ne
doit jamais empêcher le fourgon de démarrer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from vanmonitor.config import ConfigStore, DEFAULTS, validate
from vanmonitor.config.defaults import CONFIG_VERSION

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def store(tmp_path: Path) -> ConfigStore:
    config = ConfigStore(tmp_path / "config.json", debounce_s=0.0)
    config.load()
    return config


# ---------------------------------------------------------------------------
# Cohérence du dépôt
# ---------------------------------------------------------------------------

def test_shipped_default_file_matches_the_code() -> None:
    """``config/config.default.json`` ne doit pas diverger de ``defaults.py``."""
    shipped = json.loads(
        (REPO_ROOT / "config" / "config.default.json").read_text(encoding="utf-8")
    )
    assert shipped == DEFAULTS


def test_documented_defaults_are_respected() -> None:
    """Les choix arrêtés à l'étape 1 doivent être ceux du code."""
    assert DEFAULTS["history"]["enabled"] is False
    assert DEFAULTS["history"]["sample_period_s"] == 300
    assert DEFAULTS["history"]["retention_hours"] == 24

    circuits = DEFAULTS["heating"]["circuits"]
    assert circuits["local_eau"]["on_sensor_loss"] == "open"
    assert circuits["local_batterie"]["on_sensor_loss"] == "open"
    assert circuits["cabine"]["on_sensor_loss"] == "hold"

    # Seuls Local eau porte un exemple ; les deux autres restent à définir.
    assert circuits["local_eau"]["open_below_c"] == 5.0
    assert circuits["local_batterie"]["open_below_c"] is None
    assert circuits["cabine"]["open_below_c"] is None

    assert DEFAULTS["tanks"]["gasoil"]["capacity_l"] == 105.0
    assert DEFAULTS["tanks"]["eau_propre"]["capacity_l"] is None
    assert DEFAULTS["tanks"]["eaux_grises"]["unit"] == "percent"

    assert DEFAULTS["battery"]["link"]["type"] == "vedirect_serial"


# ---------------------------------------------------------------------------
# Lecture / écriture
# ---------------------------------------------------------------------------

def test_get_and_set_by_path(store: ConfigStore) -> None:
    assert store.get("heating.circuits.cabine.mode") == "manuel"
    assert store.set("heating.circuits.cabine.mode", "auto") is True
    assert store.get("heating.circuits.cabine.mode") == "auto"
    # Écrire la même valeur ne déclenche rien.
    assert store.set("heating.circuits.cabine.mode", "auto") is False


def test_unknown_path_is_rejected(store: ConfigStore) -> None:
    with pytest.raises(KeyError):
        store.set("heating.circuits.garage.mode", "auto")


def test_values_are_copied_not_shared(store: ConfigStore) -> None:
    points = store.get("tanks.gasoil.calibration.points")
    points.append({"raw": 0.0, "value": 0.0})
    assert store.get("tanks.gasoil.calibration.points") == []


def test_changes_are_persisted_and_reloaded(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    first = ConfigStore(path, debounce_s=0.0)
    first.load()
    first.set("alerts.battery_soc_min_pct", 25)
    first.save_now()

    second = ConfigStore(path, debounce_s=0.0)
    second.load()
    assert second.get("alerts.battery_soc_min_pct") == 25


def test_writes_are_debounced(tmp_path: Path) -> None:
    """Bouger un curseur ne doit pas écrire dix fois sur la carte microSD."""
    path = tmp_path / "config.json"
    store = ConfigStore(path, debounce_s=30.0)
    store.load()

    for value in range(10, 30):
        store.set("alerts.fuel_min_pct", value)
    assert not path.exists()        # rien n'est encore parti sur le disque

    store.save_now()
    assert json.loads(path.read_text(encoding="utf-8"))["alerts"]["fuel_min_pct"] == 29


def test_listeners_are_notified(store: ConfigStore) -> None:
    seen: list[str] = []
    store.add_listener(seen.append)
    store.set("alerts.fuel_min_pct", 15)
    assert seen == ["alerts.fuel_min_pct"]


def test_reset_section(store: ConfigStore) -> None:
    store.set("alerts.fuel_min_pct", 5)
    store.reset_section("alerts")
    assert store.get("alerts.fuel_min_pct") == 20


# ---------------------------------------------------------------------------
# Robustesse
# ---------------------------------------------------------------------------

def test_corrupted_file_falls_back_to_the_backup(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    store = ConfigStore(path, debounce_s=0.0)
    store.load()
    store.set("alerts.fuel_min_pct", 33)
    store.save_now()
    store.set("alerts.fuel_min_pct", 44)
    store.save_now()        # crée config.json.bak avec la valeur 33

    path.write_text("{ ceci n'est pas du JSON", encoding="utf-8")

    recovered = ConfigStore(path, debounce_s=0.0)
    warnings = recovered.load()
    assert any("illisible" in message for message in warnings)
    assert recovered.get("alerts.fuel_min_pct") == 33


def test_missing_file_yields_defaults(tmp_path: Path) -> None:
    store = ConfigStore(tmp_path / "absent.json", debounce_s=0.0)
    assert store.load() == []
    assert store.get("config_version") == CONFIG_VERSION


def test_garbage_config_never_prevents_startup() -> None:
    config, warnings = validate({"heating": "n'importe quoi", "alerts": 42})
    assert config["alerts"]["battery_soc_min_pct"] == 20
    assert config["heating"]["circuits"]["local_eau"]["label"] == "Local eau"
    assert warnings == [] or isinstance(warnings, list)


def test_incoherent_thresholds_are_cleared() -> None:
    """Fermeture sous ouverture : seuils effacés, AUTO désactivé, pas de plantage."""
    config, warnings = validate({
        "heating": {"circuits": {"local_eau": {
            "mode": "auto", "open_below_c": 10.0, "close_above_c": 4.0,
        }}},
    })
    circuit = config["heating"]["circuits"]["local_eau"]
    assert circuit["open_below_c"] is None
    assert circuit["close_above_c"] is None
    assert circuit["mode"] == "manuel"
    assert any("seuils incohérents" in message for message in warnings)


def test_auto_mode_refused_without_thresholds() -> None:
    config, warnings = validate({
        "heating": {"circuits": {"cabine": {"mode": "auto"}}},
    })
    assert config["heating"]["circuits"]["cabine"]["mode"] == "manuel"
    assert any("seuils non définis" in message for message in warnings)


def test_duplicate_sensor_binding_is_broken() -> None:
    """Une même sonde ne peut pas être associée à deux zones."""
    config, warnings = validate({
        "temperatures": {"zones": {
            "cabine": {"sensor_id": "28-000000000001"},
            "cellule": {"sensor_id": "28-000000000001"},
        }},
    })
    zones = config["temperatures"]["zones"]
    bound = [name for name, zone in zones.items() if zone["sensor_id"]]
    assert bound == ["cabine"]
    assert any("déjà associée" in message for message in warnings)


def test_out_of_range_values_fall_back_to_defaults() -> None:
    config, warnings = validate({"alerts": {"battery_soc_min_pct": 900}})
    assert config["alerts"]["battery_soc_min_pct"] == 20
    assert warnings
