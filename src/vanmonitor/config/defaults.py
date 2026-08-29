"""Valeurs par défaut de la configuration.

Ce dictionnaire est la **source unique de vérité**. Le fichier
``config/config.default.json`` du dépôt en est une copie lisible, et un test
(`tests/test_config_store.py`) vérifie que les deux ne divergent pas.

Rappels importants :

* les seuils de chauffage ci-dessous sont des **valeurs d'exemple**, pas des
  choix définitifs. Seul ``local_eau`` en porte un ; ``local_batterie`` et
  ``cabine`` valent ``null`` — à définir, mode AUTO indisponible d'ici là ;
* l'historique est **désactivé par défaut** ;
* la capacité de l'eau propre est inconnue : elle sera déduite du plus haut
  point de calibration.
"""

from __future__ import annotations

import copy
from typing import Any

CONFIG_VERSION = 1

DEFAULTS: dict[str, Any] = {
    "config_version": CONFIG_VERSION,

    "general": {
        "simulation": False,
        "fullscreen": True,
        "ui_refresh_hz": 2,
        # Diagonale physique de la dalle, en pouces. Sert à dimensionner
        # les cibles tactiles en millimètres réels et non en pixels.
        # MATERIEL À INTEGRER PLUS TARD : écran non choisi (H-6).
        # Waveshare 5 pouces HDMI LCD (H) V4 : 800 x 480, tactile capacitif,
        # image par HDMI, tactile par USB, orientation paysage.
        "screen_diagonal_in": 5.0,
    },

    "display": {
        # La veille ne concerne que la dalle : le Raspberry, les acquisitions,
        # le chauffage et les alertes continuent de tourner.
        "sleep_enabled": True,
        "sleep_delay_s": 300,
        # "auto" essaie vcgencmd, puis xset, puis le rétroéclairage.
        # Forçable si le comportement réel du Waveshare le demande.
        "sleep_method": "auto",
    },

    "workers": {
        "watchdog_factor": 3,
        "restart_backoff_s": [5, 15, 60, 300],
    },

    "temperatures": {
        "poll_period_s": 10,
        "read_timeout_s": 3.0,
        "stale_after_s": 60,
        "valid_range_c": [-40.0, 85.0],
        # Écart maximal admis entre deux lectures successives d'une même
        # sonde. Au-delà, la valeur attend confirmation par la mesure
        # suivante : un bus 1-Wire secoué produit des trames aberrantes.
        "max_step_c": 12.0,
        "zones": {
            "local_batterie": {
                "label": "Local batterie", "sensor_id": None,
                "offset_c": 0.0, "critical": True,
            },
            "local_eau": {
                "label": "Local eau", "sensor_id": None,
                "offset_c": 0.0, "critical": True,
            },
            "coffre": {
                "label": "Coffre", "sensor_id": None,
                "offset_c": 0.0, "critical": False,
            },
            "cabine": {
                "label": "Cabine", "sensor_id": None,
                "offset_c": 0.0, "critical": True,
            },
            "cellule": {
                "label": "Cellule", "sensor_id": None,
                "offset_c": 0.0, "critical": False,
            },
        },
    },

    "tanks": {
        "poll_period_s": 2,
        "read_timeout_s": 1.0,
        "stale_after_s": 30,
        "filter": {"median_window": 5, "ema_alpha": 0.2},

        "eau_propre": {
            "label": "Eau propre",
            "display": ["litres", "percent"],
            "unit": "litres",
            # Capacité inconnue : déduite du plus haut point de calibration.
            "capacity_l": None,
            "channel": "CH0",            # MATERIEL À INTEGRER PLUS TARD
            "calibration": {"points": [], "updated_at": None},
        },
        "eaux_grises": {
            "label": "Eaux grises",
            "display": ["percent"],
            "unit": "percent",           # calibration directement en pourcentage
            "capacity_l": None,
            "channel": "CH1",            # MATERIEL À INTEGRER PLUS TARD
            "calibration": {"points": [], "updated_at": None},
        },
        "gasoil": {
            "label": "Gasoil",
            "display": ["litres", "percent"],
            "unit": "litres",
            "capacity_l": 105.0,         # capacité connue et déclarée
            "channel": "CH2",            # MATERIEL À INTEGRER PLUS TARD
            "calibration": {"points": [], "updated_at": None},
        },
    },

    "battery": {
        "poll_period_s": 1.0,
        "stale_after_s": 15,
        "read_timeout_s": 2.0,
        "reconnect_backoff_s": [1, 2, 5, 10, 30],
        "show_time_to_go": True,
        "time_to_go_max_valid_min": 6000,
        "link": {
            # Liaison retenue : VE.Direct filaire → interface VE.Direct/USB.
            "type": "vedirect_serial",
            "port": None,                # /dev/serial/by-id/… à figer à la mise en service
            "baudrate": 19200,           # conforme à la documentation VE.Direct, à confirmer
        },
    },

    "heating": {
        "control_period_s": 5,
        "min_state_dwell_s": 120,
        "transition_timeout_s": 60,
        "min_threshold_delta_c": 1.0,
        "command_timeout_s": 5.0,
        "circuits": {
            "local_eau": {
                "label": "Local eau",
                "zone": "local_eau",
                "mode": "auto",
                "open_below_c": 5.0,     # EXEMPLE, modifiable à l'écran
                "close_above_c": 8.0,    # EXEMPLE, modifiable à l'écran
                "on_sensor_loss": "open",
                "driver": {"type": "mock", "params": {}},   # MATERIEL À INTEGRER PLUS TARD
            },
            "local_batterie": {
                "label": "Local batterie",
                "zone": "local_batterie",
                "mode": "manuel",
                "open_below_c": None,    # À DÉFINIR — AUTO indisponible tant que null
                "close_above_c": None,
                "on_sensor_loss": "open",
                "driver": {"type": "mock", "params": {}},
            },
            "cabine": {
                "label": "Cabine",
                "zone": "cabine",
                "mode": "manuel",
                "open_below_c": None,    # À DÉFINIR
                "close_above_c": None,
                "on_sensor_loss": "hold",
                "driver": {"type": "mock", "params": {}},
            },
        },
    },

    "alerts": {
        "battery_soc_min_pct": 20,
        "fresh_water_min_pct": 20,
        "fuel_min_pct": 20,
        "grey_water_max_pct": 80,
        "rearm_margin_pct": 3,
        "min_duration_s": 30,
        "technical_alerts": True,
    },

    "history": {
        "enabled": False,                # DÉSACTIVÉ PAR DÉFAUT
        "sample_period_s": 300,          # 5 minutes si activé
        "retention_hours": 24,
        "db_path": "/var/lib/vanmonitor/history.db",
        "batch_size": 10,
    },

    "logging": {
        "level": "INFO",
        "dedup_window_s": 300,
    },
}


def default_config() -> dict[str, Any]:
    """Copie profonde des valeurs par défaut (jamais la référence partagée)."""
    return copy.deepcopy(DEFAULTS)
