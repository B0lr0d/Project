# Système de monitoring et de commande — Fourgon aménagé

**ÉTAPE 1 — Architecture proposée (aucun code applicatif).**
Document à valider avant tout développement.

Cible : Raspberry Pi 4, Raspberry Pi OS (64 bits), écran tactile 4,3"–5"
(résolution de référence **800 × 480**), fonctionnement 100 % local, sans Internet.

Convention utilisée dans tout le document :
tout élément matériel non confirmé est marqué **MATERIEL À INTEGRER PLUS TARD**
et n'existe côté logiciel que sous forme d'interface abstraite.

---

## 1. Architecture logicielle

### 1.1 Principe général

Quatre couches strictement séparées, avec une dépendance à sens unique :

```
        ┌──────────────────────────────────────────────┐
        │  UI (PyQt5)  — Accueil, Paramètres, Sim      │   ne connaît AUCUN matériel
        └───────────────▲──────────────────┬───────────┘
        snapshot (signal)│                  │ commandes (queue)
        ┌───────────────┴──────────────────▼───────────┐
        │  CORE — logique métier                       │   ne connaît AUCUN matériel
        │  calibration · chauffage · alertes ·         │
        │  historique · état · santé                   │
        └───────────────▲──────────────────┬───────────┘
                 valeurs│                  │ ordres
        ┌───────────────┴──────────────────▼───────────┐
        │  HAL — interfaces abstraites                 │   seule couche qui parle
        │  real/  (Raspberry)      sim/  (mocks)       │   au matériel
        └──────────────────────────────────────────────┘
                        ▲
        ┌───────────────┴──────────────────────────────┐
        │  CONFIG — fichier JSON unique, persistant    │
        └──────────────────────────────────────────────┘
```

Règles non négociables :

1. `core/` et `ui/` n'importent **jamais** `hal/real/`. Ils ne connaissent que
   `hal/interfaces.py`.
2. L'instanciation du matériel réel ou simulé se fait **uniquement** dans
   `hal/factory.py`, appelé par le point d'entrée `app.py` (composition root).
3. Passer de la simulation au matériel réel = changer une clé de configuration
   ou un argument de ligne de commande. Aucune autre ligne de code ne change.
4. Toute lecture matérielle est faite dans un thread de fond. L'interface
   graphique ne fait **aucune** I/O.

### 1.2 Modèle de threads

| Thread | Rôle | Cadence |
|---|---|---|
| **Thread UI (principal Qt)** | affichage, saisies tactiles | rafraîchissement 2 Hz |
| **Thread acquisition** | lecture capteurs, logique chauffage, alertes | tick 1 s, tâches à périodes propres |
| **Thread historique** | écriture SQLite par lots | réveil toutes les N minutes |

Le thread d'acquisition exécute un **ordonnanceur coopératif** : chaque tâche a
sa propre période (températures 10 s, niveaux 2 s, batterie 1 s, chauffage 5 s).
Une tâche lente ou en erreur ne bloque pas les autres : chaque tâche est
exécutée dans un `try/except` avec chronomètre et compteur d'échecs.

Communication inter-threads :
* **acquisition → UI** : un objet immuable `SystemSnapshot` publié via un signal
  Qt (connexion `QueuedConnection`, thread-safe par construction).
* **UI → acquisition** : file `queue.Queue` de commandes (`Command`), consommée
  au début de chaque tick. Aucune UI ne pilote directement une vanne.

### 1.3 Machine à états et sécurité

* Chaque grandeur portée par le snapshot possède un **statut** :
  `OK` · `STALE` (donnée trop vieille) · `FAULT` (erreur de lecture) ·
  `ABSENT` (capteur non configuré / non détecté).
* L'UI affiche `--` pour `ABSENT`/`STALE` et `Erreur capteur` pour `FAULT`.
* La logique chauffage refuse de commander en AUTO sur une température non `OK` :
  comportement configurable `heating.on_sensor_loss` (`hold` par défaut) +
  alerte technique. **Point à valider (voir §10, R-07).**

---

## 2. Arborescence complète des fichiers

```
Project/
├── README.md
├── pyproject.toml                     # métadonnées + dépendances
├── requirements.txt                   # variante « pip pur » (PC)
├── requirements-pi.txt                # variante Raspberry (apt + pip)
│
├── docs/
│   ├── ARCHITECTURE.md                # ce document
│   ├── HARDWARE_TODO.md               # liste des points « MATERIEL À INTEGRER PLUS TARD »
│   ├── MOCKUP_ACCUEIL.md
│   ├── MOCKUP_PARAMETRES.md
│   └── INSTALL_RASPBERRY.md
│
├── config/
│   └── config.default.json            # valeurs par défaut livrées (jamais modifié à l'exécution)
│
├── deploy/
│   ├── vanmonitor.service             # unité systemd (autostart + restart)
│   ├── install.sh                     # installation/dépendances
│   └── kiosk.md                       # notes plein écran / masquage curseur
│
├── src/vanmonitor/
│   ├── __init__.py
│   ├── __main__.py                    # python -m vanmonitor
│   ├── cli.py                         # arguments : --sim, --windowed, --config
│   ├── app.py                         # COMPOSITION ROOT : assemble config + HAL + core + UI
│   ├── constants.py                   # énumérations : ZoneId, CircuitId, TankId, Status, ValveState, Mode
│   ├── models.py                      # dataclasses immuables du snapshot
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── defaults.py                # dictionnaire des valeurs par défaut
│   │   ├── schema.py                  # validation + migration de version
│   │   └── store.py                   # ConfigStore : chargement, écriture atomique, sauvegarde différée
│   │
│   ├── hal/
│   │   ├── __init__.py
│   │   ├── interfaces.py              # TemperatureSensor, LevelSensor, ADCInterface,
│   │   │                              # SmartShuntInterface, ValveDriver, ClockSource
│   │   ├── factory.py                 # build_hal(config, simulation: bool) -> HalBundle
│   │   ├── real/
│   │   │   ├── __init__.py
│   │   │   ├── ds18b20.py             # 1-Wire via /sys/bus/w1/devices
│   │   │   ├── adc_level.py           # MATERIEL À INTEGRER PLUS TARD (convertisseur non choisi)
│   │   │   ├── smartshunt_link.py     # MATERIEL À INTEGRER PLUS TARD (liaison non choisie)
│   │   │   └── valve_driver.py        # MATERIEL À INTEGRER PLUS TARD (actionneur non choisi)
│   │   └── sim/
│   │       ├── __init__.py
│   │       ├── sim_state.py           # état simulé partagé, piloté par le panneau de simulation
│   │       ├── mock_temperature.py    # MockTemperatureSensor
│   │       ├── mock_level.py          # MockLevelSensor
│   │       ├── mock_smartshunt.py     # MockSmartShuntInterface
│   │       └── mock_valve.py          # MockValveDriver
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── state.py                   # StateStore : dernier snapshot, accès verrouillé
│   │   ├── scheduler.py               # AcquisitionWorker + PeriodicTask
│   │   ├── commands.py                # CommandBus + dataclasses de commandes
│   │   ├── health.py                  # suivi fraîcheur/fautes, anti-rebond
│   │   ├── calibration.py             # CalibrationTable (interpolation multipoints)
│   │   ├── filters.py                 # filtre médian + moyenne exponentielle
│   │   ├── temperature_service.py     # TemperatureService
│   │   ├── tank_service.py            # TankService (eau propre, eaux grises, gasoil)
│   │   ├── battery_service.py         # BatteryService (SmartShunt)
│   │   ├── heating.py                 # HeatingCircuit + HeatingController (hystérésis)
│   │   ├── alerts.py                  # AlertEngine + règles
│   │   └── history.py                 # HistoryRecorder (SQLite, désactivable)
│   │
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── main_window.py             # QStackedWidget : Accueil / Paramètres
│   │   ├── theme.py                   # palette, tailles, polices
│   │   ├── style.qss                  # feuille de style sombre
│   │   ├── home_page.py
│   │   ├── settings_page.py           # rail latéral + pages de réglages
│   │   ├── settings/
│   │   │   ├── heating_settings.py
│   │   │   ├── alerts_settings.py
│   │   │   ├── calibration_settings.py
│   │   │   ├── sensors_settings.py    # association des identifiants DS18B20
│   │   │   └── history_settings.py
│   │   ├── widgets/
│   │   │   ├── tile.py                # tuile de mesure (grand chiffre + unité)
│   │   │   ├── bar_gauge.py           # jauge horizontale
│   │   │   ├── temperature_list.py
│   │   │   ├── circuit_row.py         # ligne chauffage (nom, mode, état, actions)
│   │   │   ├── alert_bar.py
│   │   │   ├── numeric_keypad.py      # pavé numérique tactile (saisie de seuils)
│   │   │   └── touch_controls.py      # boutons/bascules ≥ 64 px
│   │   └── sim_panel.py               # fenêtre de simulation (curseurs + interrupteurs de panne)
│   │
│   └── util/
│       ├── __init__.py
│       ├── logging_setup.py           # logs vers journald, niveau configurable
│       ├── ratelimit.py               # anti-spam de logs (dédoublonnage par message)
│       └── timebase.py                # horloge monotone pour la logique, horloge murale pour l'affichage
│
└── tests/
    ├── test_calibration.py            # interpolation, hors plage, incohérences
    ├── test_heating.py                # hystérésis, anti-cyclage, pannes capteur
    ├── test_alerts.py                 # seuils, réarmement, alertes techniques
    ├── test_config_store.py           # écriture atomique, migration, fichier corrompu
    ├── test_tank_service.py
    └── test_resilience.py             # panne d'un capteur → le reste continue
```

---

## 3. Bibliothèques proposées

### 3.1 Retenues

| Bibliothèque | Usage | Justification |
|---|---|---|
| **Python 3.11** (fourni par Raspberry Pi OS Bookworm) | — | pas de compilation, `dataclasses`, typage |
| **PyQt5** (`apt install python3-pyqt5`) | interface graphique | paquet système testé et stable sur Raspberry Pi OS, aucun problème d'architecture ARM, plein écran natif, tactile natif, styles QSS très proches du CSS. Fonctionne à l'identique sur PC. |
| **sqlite3** (bibliothèque standard) | historique | zéro dépendance, fichier unique, requêtes simples |
| **json** (bibliothèque standard) | configuration | lisible, éditable à la main en cas de dépannage |
| **logging** (bibliothèque standard) | journalisation | sortie vers journald, aucune écriture SD dédiée |
| **pytest** | tests | seulement en développement |

### 3.2 Ajoutées seulement quand le matériel sera choisi

| Bibliothèque | Condition |
|---|---|
| `pyserial` | si la liaison SmartShunt retenue est **VE.Direct filaire** |
| bibliothèque BLE (`bleak` ou équivalent) | si la liaison retenue est **Bluetooth** |
| bibliothèque du convertisseur analogique-numérique | dépend du modèle choisi |
| `gpiozero` / `lgpio` | seulement si les actionneurs de vannes sont pilotés par GPIO |

Ces dépendances seront **optionnelles** : leur absence ne doit pas empêcher
l'application de démarrer (import différé, à l'intérieur du module `hal/real/`
concerné uniquement).

### 3.3 Écartées volontairement

* **Aucun serveur web, aucun navigateur** (Flask/Chromium) : consommation
  mémoire et complexité inutiles, démarrage plus lent, dépendance à un
  navigateur en mode kiosque.
* **Aucune base de données autre que SQLite.**
* **Aucun broker de messages** (MQTT) : tout est dans un seul processus.
* **PySide6** est une alternative acceptable (licence LGPL plutôt que GPL) mais
  l'installation sur ARM est moins prévisible ; à retenir seulement si la
  licence GPL de PyQt5 pose problème. **Point à valider.**

### 3.4 Alternative à l'interface graphique

Alternative si PyQt5 pose problème sur ton écran : **Kivy** (accélération GPU,
tactile excellent) — plus lourd à styler, dépendances plus délicates.
Recommandation ferme : **PyQt5**.

---

## 4. Structure des classes principales

Signatures uniquement — aucune implémentation à ce stade.

### 4.1 Énumérations et modèles (`constants.py`, `models.py`)

```python
class Status(Enum):        OK, STALE, FAULT, ABSENT
class ValveState(Enum):    OUVERT, FERME, OUVERTURE, FERMETURE, ERREUR, INCONNU
class HeatingMode(Enum):   AUTO, MANUEL
class ZoneId(Enum):        LOCAL_BATTERIE, LOCAL_EAU, COFFRE, CABINE, CELLULE
class CircuitId(Enum):     LOCAL_EAU, LOCAL_BATTERIE, CABINE
class TankId(Enum):        EAU_PROPRE, EAUX_GRISES, GASOIL
class AlertLevel(Enum):    INFO, WARN, CRITIQUE
```

```python
@dataclass(frozen=True)
class TemperatureReading:
    zone: ZoneId
    label: str                 # "Local batterie"
    celsius: float | None
    status: Status
    updated_at: float          # horloge monotone

@dataclass(frozen=True)
class TankReading:
    tank: TankId
    label: str
    litres: float | None       # None pour les eaux grises si non calibré en litres
    percent: float | None
    raw: float | None
    status: Status
    out_of_range: bool
    updated_at: float

@dataclass(frozen=True)
class BatteryReading:
    soc_percent: float | None
    voltage_v: float | None
    current_a: float | None
    power_w: float | None
    consumed_ah: float | None
    time_to_go_min: int | None     # None si non fourni ou jugé non fiable
    status: Status
    updated_at: float

@dataclass(frozen=True)
class CircuitStatus:
    circuit: CircuitId
    label: str                 # "Local eau" — jamais "Circuit 1"
    mode: HeatingMode
    state: ValveState
    zone: ZoneId
    temperature_c: float | None
    open_below_c: float
    close_above_c: float
    fault: bool
    fault_reason: str | None
    since: float

@dataclass(frozen=True)
class Alert:
    key: str                   # "eau_propre_basse"
    level: AlertLevel
    message: str               # "Eau propre 18 %"
    active_since: float

@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: float
    wall_time: float
    temperatures: dict[ZoneId, TemperatureReading]
    tanks: dict[TankId, TankReading]
    battery: BatteryReading
    circuits: dict[CircuitId, CircuitStatus]
    alerts: tuple[Alert, ...]
    simulation: bool
```

### 4.2 Interfaces matérielles (`hal/interfaces.py`)

```python
class TemperatureSensor(ABC):
    @abstractmethod
    def read_celsius(self) -> float: ...        # lève SensorError si indisponible
    @abstractmethod
    def sensor_id(self) -> str: ...
    @abstractmethod
    def is_present(self) -> bool: ...

class ADCInterface(ABC):
    """Convertisseur analogique-numérique. MATERIEL À INTEGRER PLUS TARD."""
    @abstractmethod
    def read_channel(self, channel: str) -> float: ...   # valeur brute, unité non interprétée
    @abstractmethod
    def channels(self) -> list[str]: ...

class LevelSensor(ABC):
    """Capteur de niveau. Rend une valeur BRUTE, jamais des litres."""
    @abstractmethod
    def read_raw(self) -> float: ...
    @abstractmethod
    def is_present(self) -> bool: ...

class SmartShuntInterface(ABC):
    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def disconnect(self) -> None: ...
    @abstractmethod
    def read(self) -> BatteryReading: ...
    @abstractmethod
    def is_connected(self) -> bool: ...

class ValveDriver(ABC):
    @abstractmethod
    def open(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...
    @abstractmethod
    def get_state(self) -> ValveState: ...
    @abstractmethod
    def has_fault(self) -> bool: ...
```

`SensorError`, `LinkError`, `ValveError` : exceptions dédiées définies dans
`hal/interfaces.py`. Aucune couche supérieure ne rattrape `Exception` nue.

### 4.3 Calibration (`core/calibration.py`)

```python
@dataclass(frozen=True)
class CalibrationPoint:
    raw: float
    value: float               # litres OU pourcentage selon l'unité du réservoir

class CalibrationError(ValueError): ...

class CalibrationTable:
    def __init__(self, points: list[CalibrationPoint], unit: str, capacity: float | None): ...

    @classmethod
    def from_config(cls, data: dict) -> "CalibrationTable": ...
    def to_config(self) -> dict: ...

    def validate(self) -> None: ...
        # - au moins 2 points
        # - valeurs brutes strictement monotones (croissantes OU décroissantes)
        # - valeurs converties monotones dans le même sens
        # - aucun doublon de valeur brute
        # - volumes compris entre 0 et la capacité déclarée
        # → lève CalibrationError avec un message affichable à l'écran

    def convert(self, raw: float) -> tuple[float, bool]: ...
        # interpolation linéaire par segments ; retourne (valeur, hors_plage)
        # hors plage = valeur bornée au point extrême + drapeau à True

    def percent(self, raw: float) -> tuple[float, bool]: ...
    def is_calibrated(self) -> bool: ...
```

Les points sont **triés à la construction**. Aucune extrapolation : au-delà du
dernier point, la valeur est bornée et `out_of_range` passe à `True`
(l'UI affiche alors la valeur avec un discret repère « hors plage »).

### 4.4 Services métier (`core/`)

```python
class TemperatureService:
    def __init__(self, sensors: dict[ZoneId, TemperatureSensor], config: ConfigStore): ...
    def poll(self) -> dict[ZoneId, TemperatureReading]: ...     # n'échoue jamais globalement
    def rebind(self) -> None: ...                               # après changement d'association
    def scan_available_sensor_ids(self) -> list[str]: ...       # pour la page Paramètres

class TankService:
    def __init__(self, sensors: dict[TankId, LevelSensor], config: ConfigStore): ...
    def poll(self) -> dict[TankId, TankReading]: ...
    def read_raw(self, tank: TankId) -> float | None: ...       # utilisé pendant la calibration
    def set_calibration(self, tank: TankId, table: CalibrationTable) -> None: ...

class BatteryService:
    def __init__(self, link: SmartShuntInterface, config: ConfigStore): ...
    def poll(self) -> BatteryReading: ...
        # reconnexion avec temporisation croissante (1 s → 30 s max), sans boucle bloquante

class HeatingCircuit:
    def __init__(self, circuit_id, label, driver: ValveDriver, config): ...
    def tick(self, temperature: TemperatureReading, now: float) -> CircuitStatus: ...
    def request_manual(self, action: str) -> None: ...          # "open" | "close" | "stop"
    def set_mode(self, mode: HeatingMode) -> None: ...
    def set_thresholds(self, open_below_c: float, close_above_c: float) -> None: ...

class HeatingController:
    def __init__(self, circuits: dict[CircuitId, HeatingCircuit]): ...
    def tick(self, temperatures, now) -> dict[CircuitId, CircuitStatus]: ...

class AlertEngine:
    def __init__(self, config: ConfigStore): ...
    def evaluate(self, snapshot_parts) -> tuple[Alert, ...]: ...

class HistoryRecorder:
    def __init__(self, config: ConfigStore): ...
    def maybe_record(self, snapshot: SystemSnapshot) -> None: ...  # respecte la période
    def purge(self) -> None: ...
    def query(self, since: float) -> list[dict]: ...
    def close(self) -> None: ...
    # si history.enabled == False : aucun fichier ouvert, aucune écriture, aucune erreur
```

### 4.5 Configuration (`config/store.py`)

```python
class ConfigStore(QObject):
    changed = pyqtSignal(str)     # chemin de la clé modifiée, ex. "heating.circuits.cabine"

    def __init__(self, path: Path): ...
    def load(self) -> None: ...            # défauts → fichier → validation → migration
    def get(self, path: str, default=None): ...
    def set(self, path: str, value) -> None: ...     # applique en mémoire + planifie l'écriture
    def save_now(self) -> None: ...        # écriture atomique : .tmp → fsync → os.replace
    def reset_section(self, path: str) -> None: ...
```

Écriture **différée de 2 secondes** et regroupée : déplacer un curseur ne
déclenche qu'une seule écriture disque. Sauvegarde d'un exemplaire `config.bak`
avant remplacement ; en cas de fichier corrompu au démarrage, repli sur `.bak`
puis sur les valeurs par défaut, avec alerte technique.

---

## 5. Structure du fichier de configuration

Emplacement : `/var/lib/vanmonitor/config.json` (persistant, hors dépôt).
`config/config.default.json` du dépôt sert uniquement de gabarit initial.

```jsonc
{
  "config_version": 1,

  "general": {
    "simulation": false,
    "fullscreen": true,
    "ui_refresh_hz": 2,
    "brightness_control": false
  },

  "temperatures": {
    "poll_period_s": 10,
    "stale_after_s": 60,
    "valid_range_c": [-40.0, 85.0],
    "zones": {
      "local_batterie": { "label": "Local batterie", "sensor_id": null, "offset_c": 0.0, "critical": true },
      "local_eau":      { "label": "Local eau",      "sensor_id": null, "offset_c": 0.0, "critical": true },
      "coffre":         { "label": "Coffre",         "sensor_id": null, "offset_c": 0.0, "critical": false },
      "cabine":         { "label": "Cabine",         "sensor_id": null, "offset_c": 0.0, "critical": true },
      "cellule":        { "label": "Cellule",        "sensor_id": null, "offset_c": 0.0, "critical": false }
    }
  },

  "tanks": {
    "poll_period_s": 2,
    "filter": { "median_window": 5, "ema_alpha": 0.2 },
    "eau_propre": {
      "label": "Eau propre",
      "display": ["litres", "percent"],
      "unit": "litres",
      "capacity_l": null,                        // À CONFIRMER (volume total du réservoir)
      "channel": "CH0",                          // MATERIEL À INTEGRER PLUS TARD
      "calibration": { "points": [], "updated_at": null }
    },
    "eaux_grises": {
      "label": "Eaux grises",
      "display": ["percent"],
      "unit": "percent",
      "capacity_l": null,
      "channel": "CH1",                          // MATERIEL À INTEGRER PLUS TARD
      "calibration": { "points": [], "updated_at": null }
    },
    "gasoil": {
      "label": "Gasoil",
      "display": ["litres", "percent"],
      "unit": "litres",
      "capacity_l": 105.0,
      "channel": "CH2",                          // MATERIEL À INTEGRER PLUS TARD
      "calibration": { "points": [], "updated_at": null }
    }
  },

  "battery": {
    "poll_period_s": 1,
    "stale_after_s": 15,
    "reconnect_backoff_s": [1, 2, 5, 10, 30],
    "show_time_to_go": true,
    "time_to_go_max_valid_min": 6000,
    "link": {
      "type": "mock",                            // MATERIEL À INTEGRER PLUS TARD
      "port": null,
      "address": null
    }
  },

  "heating": {
    "control_period_s": 5,
    "min_state_dwell_s": 120,
    "transition_timeout_s": 60,
    "min_threshold_delta_c": 1.0,
    "on_sensor_loss": "hold",                    // "hold" | "close" | "open"  — À VALIDER
    "circuits": {
      "local_eau": {
        "label": "Local eau", "zone": "local_eau", "mode": "auto",
        "open_below_c": 5.0, "close_above_c": 8.0,
        "driver": { "type": "mock", "params": {} }   // MATERIEL À INTEGRER PLUS TARD
      },
      "local_batterie": {
        "label": "Local batterie", "zone": "local_batterie", "mode": "auto",
        "open_below_c": 5.0, "close_above_c": 8.0,
        "driver": { "type": "mock", "params": {} }
      },
      "cabine": {
        "label": "Cabine", "zone": "cabine", "mode": "auto",
        "open_below_c": 12.0, "close_above_c": 16.0,
        "driver": { "type": "mock", "params": {} }
      }
    }
  },

  "alerts": {
    "battery_soc_min_pct": 20,
    "fresh_water_min_pct": 20,
    "fuel_min_pct": 20,
    "grey_water_max_pct": 80,
    "rearm_margin_pct": 3,
    "min_duration_s": 30,
    "technical_alerts": true
  },

  "history": {
    "enabled": true,
    "sample_period_s": 60,
    "retention_hours": 24,
    "db_path": "/var/lib/vanmonitor/history.db",
    "batch_size": 10
  },

  "logging": {
    "level": "INFO",
    "dedup_window_s": 300
  }
}
```

Notes :

* Les seuils par défaut de chauffage ci-dessus sont des **valeurs de départ
  modifiables à l'écran**, pas des choix techniques figés.
* `min_state_dwell_s`, `transition_timeout_s`, `filter`, `reconnect_backoff_s`,
  `logging` **ne sont pas exposés dans la page Paramètres** (réglages techniques
  inutiles au conducteur), conformément à ta demande.

---

## 6. Communication entre modules

### 6.1 Boucle nominale (une seconde)

```
  ┌── AcquisitionWorker.tick() ────────────────────────────────────────┐
  │ 1. CommandBus.drain()      → applique les commandes de l'UI        │
  │ 2. TemperatureService.poll()  (si période échue)                   │
  │ 3. TankService.poll()         (si période échue)                   │
  │ 4. BatteryService.poll()      (si période échue)                   │
  │ 5. HeatingController.tick(températures)                            │
  │ 6. AlertEngine.evaluate(...)                                       │
  │ 7. StateStore.publish(SystemSnapshot)                              │
  │ 8. HistoryRecorder.maybe_record(snapshot)                          │
  └────────────────────────────────────────────────────────────────────┘
                              │ signal Qt snapshotReady
                              ▼
                    HomePage.on_snapshot(snapshot)   → redessine les tuiles
```

Chaque étape est protégée individuellement : une exception à l'étape 3
n'empêche ni l'étape 5 ni la publication du snapshot. Le service fautif
renvoie un statut `FAULT` et l'application continue.

### 6.2 Chemin d'une commande utilisateur

```
Appui « OUVRIR » sur Cabine (UI)
    → CommandBus.submit(ManualValveCommand(CircuitId.CABINE, "open"))
    → (tick suivant) HeatingCircuit.request_manual("open")
    → ValveDriver.open()
    → nouvel état lu dans get_state() → CircuitStatus → snapshot → UI
```

L'UI **n'affiche jamais un état qu'elle a supposé** : elle affiche uniquement
l'état renvoyé par le pilote au tour suivant. Un ordre sans effet reste donc
visible (état `OUVERTURE` puis `ERREUR` après expiration du délai).

### 6.3 Chemin d'un changement de réglage

```
Modification d'un seuil (UI)
    → ConfigStore.set("heating.circuits.cabine.open_below_c", 11.0)
    → validation immédiate (fermeture > ouverture + delta minimal)
    → signal changed → HeatingCircuit relit ses seuils au tick suivant
    → écriture disque différée et groupée (2 s)
```

### 6.4 Sens des dépendances (import)

```
ui       → core, config, models, constants
core     → hal.interfaces, config, models, constants
hal.real → hal.interfaces           (+ bibliothèque matérielle, import différé)
hal.sim  → hal.interfaces
app      → tout (seul endroit autorisé)
```

Un test automatique de l'arborescence des imports vérifiera qu'aucun fichier de
`core/` ou `ui/` n'importe `hal.real` ou `hal.sim`.

---

## 7. Maquette détaillée — écran Accueil

Résolution de référence **800 × 480**, thème sombre, aucune barre de défilement,
tout est visible sans interaction.

```
┌──────────────────────────────────────────────────────────────────────────┐ 0
│  FOURGON                         14:32                      ● SIM   [⚙]  │ 48 px
├───────────────────┬───────────────────┬──────────────┬───────────────────┤ 48
│ BATTERIE          │ EAU PROPRE        │ EAUX GRISES  │ GASOIL            │
│                   │                   │              │                   │
│    87 %           │    68 L           │    42 %      │    76 L           │
│  ▇▇▇▇▇▇▇▇▇░       │    68 %           │  ▇▇▇▇░░░░░░  │    72 %           │
│                   │  ▇▇▇▇▇▇▇░░░       │              │  ▇▇▇▇▇▇▇░░░       │
│  13,2 V   -4,2 A  │                   │              │                   │
│  -55 W   -12 Ah   │                   │              │                   │
│  Autonomie 18 h   │                   │              │                   │
├───────────────────┴──────────┬────────┴──────────────┴───────────────────┤ 268
│ TEMPÉRATURES                 │ CHAUFFAGE                                 │
│                              │                                           │
│ Local batterie      12,4 °C  │ Local eau        AUTO    ● OUVERT         │
│ Local eau            6,1 °C  │ Local batterie   AUTO    ○ FERMÉ          │
│ Coffre               9,8 °C  │ Cabine           MANU    ◐ OUVERTURE      │
│ Cabine              18,2 °C  │                                           │
│ Cellule                  --  │                                           │
├──────────────────────────────┴───────────────────────────────────────────┤ 436
│  ⚠  Eau propre 18 %   ·   SmartShunt non joignable                  (2)  │ 44 px
└──────────────────────────────────────────────────────────────────────────┘ 480
```

### Détail des éléments

**Bandeau supérieur (48 px)**
* Titre court à gauche, heure au centre.
* Pastille d'état à droite : `● SIM` en orange en mode simulation,
  rien en fonctionnement normal (pas de bruit visuel inutile).
* Bouton engrenage : cible tactile **64 × 48 px**, seule entrée vers Paramètres.

**Quatre tuiles principales (220 px de haut)**
* Titre en petites capitales gris clair (14 px).
* Valeur principale en **48 px gras**, lisible à 1 m.
* Valeurs secondaires en 18 px.
* Jauge horizontale de 12 px : verte, orange sous le seuil d'alerte, rouge en
  alerte. Les eaux grises suivent la logique inverse (rouge au-dessus du seuil).
* Batterie : le courant et la puissance sont **signés** (négatif = décharge) et
  colorés (bleu en charge, gris en décharge).
* Autonomie affichée **seulement** si le SmartShunt la fournit et qu'elle est
  jugée plausible ; sinon la ligne disparaît (pas de « N/A »).
* Capteur en défaut : la valeur devient `--` en gris, la jauge devient une
  bande hachurée, et le sous-titre affiche `Erreur capteur` en 14 px.

**Bloc Températures (168 px)**
* Cinq lignes fixes, toujours dans le même ordre, libellés en clair.
* Valeur alignée à droite, 22 px, une décimale.
* Sonde absente ou périmée → `--` ; sonde en défaut → `Erreur capteur`.

**Bloc Chauffage (168 px)**
* Trois lignes nommées : **Local eau**, **Local batterie**, **Cabine**.
  Jamais « Circuit 1/2/3 ».
* Colonne mode : `AUTO` ou `MANU`.
* Colonne état avec pastille de couleur :
  `● OUVERT` (orange plein) · `○ FERMÉ` (gris) · `◐ OUVERTURE` / `◑ FERMETURE`
  (animation lente) · `✕ ERREUR` (rouge) · `? INCONNU` (gris barré).
* Un appui long sur une ligne ouvre directement la page de réglage du circuit
  concerné (raccourci, pas un menu supplémentaire).

**Barre d'alertes (44 px)**
* Aucune alerte → fond neutre, texte gris : **« Aucune alerte »**.
* Une alerte → fond ambré ; plusieurs → l'alerte la plus grave est affichée,
  suivie du compteur `(2)`. Un appui déroule la liste complète en surimpression.
* Aucune animation clignotante, aucun son (à confirmer si tu veux un buzzer :
  **MATERIEL À INTEGRER PLUS TARD**).

**Palette**
| Rôle | Couleur |
|---|---|
| Fond général | `#0E1116` |
| Fond des tuiles | `#171B22` |
| Texte principal | `#F2F5F9` |
| Texte secondaire | `#8B96A5` |
| Accent / valeur normale | `#3FB950` |
| Avertissement | `#D29922` |
| Alerte | `#F04747` |
| Chauffage actif | `#F0883E` |

---

## 8. Maquette détaillée — écran Paramètres

Deux niveaux seulement : rail de sections à gauche, contenu à droite.
Aucun sous-menu au-delà.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [←] PARAMÈTRES                                                           │ 48 px
├──────────────┬───────────────────────────────────────────────────────────┤
│ CHAUFFAGE  ▸ │  CHAUFFAGE                                                │
│ ALERTES      │                                                           │
│ CALIBRATION  │  Local eau                            [ AUTO ] [ MANUEL ] │
│ SONDES       │    Ouverture   [   5,0 °C  ]                              │
│ HISTORIQUE   │    Fermeture   [   8,0 °C  ]                              │
│              │    État : OUVERT             [ OUVRIR ]  [ FERMER ]       │
│              │  ───────────────────────────────────────────────────────  │
│ 160 px       │  Local batterie                       [ AUTO ] [ MANUEL ] │
│              │    Ouverture   [   5,0 °C  ]                              │
│              │    Fermeture   [   8,0 °C  ]                              │
│              │    État : FERMÉ              [ OUVRIR ]  [ FERMER ]       │
│              │  ───────────────────────────────────────────────────────  │
│              │  Cabine                               [ AUTO ] [ MANUEL ] │
│              │    Ouverture   [  12,0 °C  ]     ▲ défilement vertical    │
└──────────────┴───────────────────────────────────────────────────────────┘
```

Règles communes à toutes les sections :

* Toute cible tactile fait au minimum **64 px de haut**.
* Un appui sur un champ numérique ouvre un **pavé numérique plein écran**
  (grand, avec `−` / `+` par pas, `Annuler` / `Valider`). Aucun clavier système.
* Les modifications sont **appliquées immédiatement** et sauvegardées après 2 s.
  Pas de bouton « Enregistrer » global, pas de risque d'oubli.
* Une valeur refusée affiche un bandeau rouge explicite sous le champ
  (ex. « La fermeture doit dépasser l'ouverture d'au moins 1 °C ») et la valeur
  précédente est conservée.

### Section CHAUFFAGE
Trois blocs nommés `Local eau`, `Local batterie`, `Cabine`.
Pour chacun : bascule AUTO/MANUEL, seuil d'ouverture, seuil de fermeture, état
courant en direct, boutons `OUVRIR` / `FERMER` **grisés en mode AUTO**.
Contrainte appliquée : `fermeture ≥ ouverture + 1 °C`.

### Section ALERTES
```
  Batterie basse         [  20 %  ]   ◀ ▶
  Eau propre basse       [  20 %  ]
  Gasoil bas             [  20 %  ]
  Eaux grises hautes     [  80 %  ]
  Alertes techniques     [ ● activées ]
  ─────────────────────────────────────
  [ Rétablir les valeurs par défaut ]
```

### Section CALIBRATION
Choix du réservoir (`Eau propre` · `Eaux grises` · `Gasoil`) puis assistant :

```
  CALIBRATION — EAU PROPRE
  Mesure brute actuelle :  0,412        (rafraîchie en direct)

  ┌──────────┬──────────┬───────────┐
  │  BRUT    │  LITRES  │           │
  ├──────────┼──────────┼───────────┤
  │  0,050   │     0    │ [Suppr.]  │
  │  0,212   │    10    │ [Suppr.]  │
  │  0,412   │    20    │ [Suppr.]  │
  └──────────┴──────────┴───────────┘

  [ + AJOUTER LE POINT ACTUEL ]      Litres : [  30  ]

  Capacité totale : [ 100 L ]
  Aperçu : brut 0,412 → 20,0 L → 20 %
  ⚠ Les points doivent être croissants (message contextuel si incohérence)

  [ EFFACER LA TABLE ]                            [ VALIDER ]
```

* La calibration se fait **réservoir en cours de remplissage** : on ajoute un
  point à chaque palier connu, l'ordre de saisie n'a pas d'importance
  (tri automatique).
* Pour les eaux grises, la colonne se nomme `%` au lieu de `LITRES`.
* Refus explicite d'enregistrer une table non monotone ou à moins de 2 points ;
  la table précédente reste active tant que la nouvelle n'est pas valide.

### Section SONDES (association DS18B20)
```
  Sondes détectées : 4                     [ RAFRAÎCHIR ]

  Local batterie   [ 28-0316A2B4C5D6  ▾ ]   12,4 °C
  Local eau        [ 28-0316A2B4E7F8  ▾ ]    6,1 °C
  Coffre           [ 28-0316A2C1029A  ▾ ]    9,8 °C
  Cabine           [ 28-0316A2D45B11  ▾ ]   18,2 °C
  Cellule          [ — non associée —  ▾ ]     --

  [ IDENTIFIER ]  ← affiche en direct la température de la sonde sélectionnée
                     pour la reconnaître en la réchauffant à la main
```

Une même sonde ne peut pas être associée à deux zones (l'autre association est
libérée automatiquement, avec message).

### Section HISTORIQUE
```
  Historique              [ ● activé  /  ○ désactivé ]
  Fréquence d'enregistrement   [  60 s  ]   (30 s · 60 s · 5 min · 15 min)
  Durée de conservation        [  24 h  ]   (6 h · 12 h · 24 h · 48 h)

  Taille actuelle de la base : 412 ko · 1 380 enregistrements
  [ EFFACER L'HISTORIQUE ]
```

Désactiver l'historique ferme la base immédiatement et n'entraîne **aucune**
autre conséquence fonctionnelle.

### Non exposé dans Paramètres (volontairement)
Périodes de scrutation, filtres, temporisations de vannes, délais de
reconnexion, niveau de journalisation, chemins de fichiers, mode simulation.
Ces réglages restent dans `config.json`, accessibles en dépannage.

---

## 9. Éléments matériels à intégrer plus tard

Tous ces points sont **abstraits** dans le logiciel dès l'étape 2 et
n'empêcheront aucun développement.

| # | Élément | Ce qui manque | Interface logicielle prévue |
|---|---|---|---|
| H-1 | **Capteurs de niveau** (eau propre, eaux grises, gasoil) | technologie et type de signal de sortie non choisis | `LevelSensor.read_raw()` — valeur brute sans unité |
| H-2 | **Convertisseur analogique-numérique** | modèle, bus et nombre de voies non choisis | `ADCInterface.read_channel(channel)` |
| H-3 | **Actionneurs des 3 circuits de chauffage** | type d'actionneur, alimentation, présence ou non d'un retour de position | `ValveDriver` (`open/close/stop/get_state/has_fault`) |
| H-4 | **Liaison avec le Victron SmartShunt** | filaire ou sans fil : non tranché | `SmartShuntInterface` + module `smartshunt_link.py` interchangeable |
| H-5 | **Câblage 1-Wire des DS18B20** | broche utilisée, longueur de bus, résistance de tirage, alimentation | chemin `/sys/bus/w1/devices` (interface noyau standard), broche déclarée en configuration |
| H-6 | **Écran tactile** | modèle et interface non confirmés | résolution lue à l'exécution, mise en page adaptative 800×480 → 1024×600 |
| H-7 | **Horloge temps réel (RTC)** | présence non confirmée | horloge monotone pour toute la logique ; horodatage mural marqué « non fiable » tant que l'heure n'est pas sûre |
| H-8 | **Alimentation et arrêt propre** | pas de dispositif de coupure contrôlée confirmé | montage disque en lecture seule + écritures atomiques (voir R-01) |
| H-9 | **Avertisseur sonore** | non demandé, non confirmé | prévu comme option de configuration, désactivé par défaut |

Pour chacun, le module `hal/real/` correspondant existera dès l'étape 2 avec un
en-tête `# MATERIEL À INTEGRER PLUS TARD` et une implémentation qui lève
proprement `NotImplementedError` — jamais un plantage de l'application.

---

## 10. Risques techniques identifiés

| # | Risque | Impact | Mesure prévue |
|---|---|---|---|
| **R-01** | **Usure et corruption de la carte microSD** (coupures d'alimentation brutales) | perte des réglages et calibrations | écriture atomique + copie de secours ; historique par lots ; journaux en mémoire volatile ; racine montée en lecture seule envisageable (`overlayfs`) avec une seule partition inscriptible pour `/var/lib/vanmonitor` |
| **R-02** | **Lecture 1-Wire bloquante** : chaque DS18B20 demande environ 750 ms de conversion ; 5 sondes en série peuvent occuper plusieurs secondes | interface figée si mal fait | lecture dans un thread dédié, jamais dans le thread UI, période 10 s ; délai maximal par sonde et abandon en cas de dépassement |
| **R-03** | **Bus 1-Wire long et bruité dans un fourgon** (vibrations, longueur, parasites) | valeurs erratiques ou sondes qui disparaissent | rejet des valeurs hors plage physique, filtre sur les variations brutales, statut `STALE`, ré-détection périodique du bus |
| **R-04** | **Remplacement d'une sonde DS18B20** : l'identifiant unique change | zone orpheline | page Paramètres avec détection et réassociation, fonction « Identifier » ; l'application démarre normalement avec une zone non associée |
| **R-05** | **Liaison SmartShunt instable** (sans fil : coupures, appairage ; filaire : port série qui change de nom) | valeurs batterie manquantes | interface isolée, reconnexion à intervalle croissant plafonné, statut `STALE` puis alerte technique, aucune boucle de reconnexion permanente |
| **R-06** | **Autonomie restante fournie par le SmartShunt peu fiable** (valeur extrême, absente) | affichage trompeur | valeur affichée seulement si présente et dans une plage plausible, sinon la ligne disparaît |
| **R-07** | **Perte d'une sonde utilisée par le chauffage** — le comportement de repli est un choix de sécurité, pas un choix technique | risque de gel ou de surchauffe | paramètre `on_sensor_loss` (`hold` par défaut) + alerte technique. **Décision à valider par toi** (voir §11) |
| **R-08** | **Absence de retour de position sur les vannes** (si le matériel n'en fournit pas) | l'état affiché serait une supposition | `ValveState` distingue explicitement `OUVERTURE`/`FERMETURE` (transition estimée par temporisation) et `INCONNU` ; passage en `ERREUR` si la transition n'aboutit pas dans le délai |
| **R-09** | **Cyclage rapide des vannes** autour d'un seuil | usure mécanique, consommation | hystérésis réelle (deux seuils distincts) **et** durée minimale de maintien d'état (120 s), contrainte `fermeture ≥ ouverture + 1 °C` imposée à la saisie |
| **R-10** | **Ballottement du carburant et du réservoir d'eau en roulant** | valeurs qui sautent | filtre médian glissant puis moyenne exponentielle ; affichage arrondi (litres entiers, pourcentages entiers) pour éviter les chiffres qui dansent |
| **R-11** | **Calibration incohérente saisie par l'utilisateur** (points non monotones, doublons) | conversion aberrante | validation stricte avant enregistrement, message explicite, conservation de la table précédente tant que la nouvelle est invalide |
| **R-12** | **Réservoir d'eau de forme irrégulière** : les extrêmes (0 % et plein) sont les plus difficiles à calibrer | affichage faux en fin de réservoir | recommandation de placer des points rapprochés en début et fin de plage ; bornage explicite et indicateur « hors plage » plutôt qu'une extrapolation inventée |
| **R-13** | **Heure système fausse sans Internet ni RTC** | historique horodaté n'importe comment | toute la logique utilise une horloge monotone ; l'historique enregistre aussi le temps écoulé depuis le démarrage ; l'heure murale est signalée comme non fiable tant qu'elle n'a pas été réglée |
| **R-14** | **Saturation des journaux** en cas de panne répétée | usure disque, journaux illisibles | dédoublonnage des messages identiques sur une fenêtre de 5 minutes, journalisation d'un résumé (« 312 occurrences ») |
| **R-15** | **Plantage de l'application** | perte totale de l'affichage et de la commande | redémarrage automatique par systemd (`Restart=always`, temporisation croissante) ; l'état des vannes au redémarrage est relu, jamais supposé |
| **R-16** | **Performances graphiques du Raspberry Pi 4** avec un rafraîchissement trop fréquent | interface saccadée, chauffe | rafraîchissement limité à 2 Hz, seuls les libellés modifiés sont redessinés, aucune animation permanente |
| **R-17** | **Divergence entre mode simulé et mode réel** (le simulateur ment) | anomalies découvertes seulement dans le fourgon | les mocks implémentent les mêmes interfaces et savent **simuler les pannes** (absence, valeur aberrante, délai, coupure de liaison, défaut de vanne), pas seulement le fonctionnement nominal |
| **R-18** | **Licence PyQt5 (GPL)** | contrainte de diffusion si le projet est publié | sans objet pour un usage personnel ; bascule vers PySide6 (LGPL) possible, l'architecture ne change pas |
| **R-19** | **Consommation électrique du Raspberry en stationnement** | décharge de la batterie auxiliaire | hors périmètre logiciel, mais l'application peut prévoir une mise en veille de l'écran ; à discuter |

---

## 11. Points à valider avant l'étape 2

1. **Capacité du réservoir d'eau propre** en litres (`105 L` est confirmé pour le
   gasoil, l'eau propre reste à préciser).
2. **Eaux grises** : calibration en pourcentage direct, ou en litres avec une
   capacité déclarée ? (Le logiciel supporte les deux ; l'affichage restera en %.)
3. **Comportement de repli du chauffage en cas de perte de sonde** (R-07) :
   maintenir l'état, fermer, ou ouvrir ?
4. **Résolution exacte de l'écran** si elle est déjà connue.
5. **PyQt5 (GPL) accepté**, ou préférence pour PySide6 (LGPL) ?
6. **Historique activé par défaut** (60 s / 24 h ≈ 1 400 enregistrements par
   jour, quelques centaines de kilo-octets) ou désactivé par défaut ?
7. **Seuils de chauffage de départ** : les valeurs proposées (5/8 °C pour les
   locaux techniques, 12/16 °C pour la cabine) te conviennent-elles comme
   valeurs initiales ?

---

## 12. Suite du projet

Aucun code applicatif n'est produit à ce stade. Après validation de ce
document, le développement suivra l'ordre convenu :

| Étape | Contenu | Livrable |
|---|---|---|
| 2 | Mode simulation | HAL complet + mocks + panneau de simulation |
| 3 | Interface graphique | Accueil et Paramètres alimentés par la simulation |
| 4 | Températures | `TemperatureService` + association des sondes |
| 5 | Niveaux et calibrations | `CalibrationTable` + assistant de calibration |
| 6 | SmartShunt | `BatteryService` + liaison réelle |
| 7 | Chauffage | `HeatingController` + hystérésis + modes |
| 8 | Alertes | `AlertEngine` |
| 9 | Historique | `HistoryRecorder` |
| 10 | Paramètres et configuration | persistance complète |
| 11 | Matériel réel | remplacement des mocks, une famille à la fois |
| 12 | Démarrage automatique | systemd, plein écran, redémarrage sur incident |
| 13 | Tests finaux | validation sur banc puis dans le fourgon |

À chaque étape : le mode simulation reste fonctionnel, l'architecture n'est pas
modifiée sans justification, et les fichiers modifiés sont fournis **entiers**
avec leur emplacement exact.
