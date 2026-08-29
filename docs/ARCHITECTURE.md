# Système de monitoring et de commande — Fourgon aménagé

**ÉTAPE 1 — Architecture proposée (aucun code applicatif).**
Révision 2 — intègre les corrections demandées après première relecture.

Cible : Raspberry Pi 4, Raspberry Pi OS (64 bits), **Waveshare 5 pouces HDMI LCD
(H) V4** — 800 × 480, tactile capacitif, image par HDMI, tactile par USB,
orientation paysage (retenu en rév. 8 ; la mise en page reste adaptative,
cet écran en devient la configuration de référence). Fonctionnement 100 % local,
sans Internet.

Convention utilisée dans tout le document :
tout élément matériel non confirmé est marqué **MATERIEL À INTEGRER PLUS TARD**
et n'existe côté logiciel que sous forme d'interface abstraite.

> **Journal des révisions**
> **Rév. 9** — vérification du geste de réveil quel que soit le flux par lequel
> la dalle tactile est exposée à Qt (tactile natif, souris synthétisée, les deux
> à la fois). Deux défauts trouvés et corrigés dans `ui/wake_guard.py` (§13,
> étape 4bis). Étape 4bis définitivement validée.
> **Rév. 8** — écran de référence figé (Waveshare 5" HDMI LCD (H) V4, H-6 clos)
> et **veille de l'écran** : seul l'affichage s'éteint, le Raspberry et tous ses
> threads restent actifs, et le premier toucher ne fait que rallumer (§13,
> étape 4bis).
> **Rév. 7** — étape 4 livrée : pilote DS18B20 réel, balayage du bus 1-Wire,
> filtrage des trames aberrantes, association et identification des sondes à
> chaud (§13). Vocabulaire des clapets aligné dans tout le document.
> **Rév. 6** — trois corrections après relecture de l'étape 3 : vocabulaire des
> clapets sans retour de position, cibles tactiles dimensionnées en
> millimètres réels, vérification de l'autonomie SmartShunt (§13).
> **Rév. 5** — écrans Accueil et Paramètres implémentés (§13, étape 3). Deux
> divergences entre la capture de référence et le texte de la demande sont
> tranchées et signalées ; la couche d'assemblage métier est posée.
> **Rév. 4** — écarts constatés pendant l'implémentation de l'étape 2
> (voir §13). Aucun changement d'architecture : deux fichiers ajoutés, une
> dépendance retirée, une règle de fraîcheur ajoutée.
> **Rév. 3** — le repli sur perte de sonde (`on_sensor_loss`) devient modifiable
> depuis la page Paramètres, circuit par circuit, sous avertissement et
> confirmation explicite. Valeurs par défaut inchangées.
> **Rév. 2** — modèle d'acquisition à threads séparés avec délais d'expiration et
> chien de garde ; liaison SmartShunt figée en VE.Direct filaire ; historique
> désactivé par défaut (5 min / 24 h) ; seuils de chauffage explicitement
> présentés comme exemples, Local batterie et Cabine à définir ; séparation
> **état commandé / état confirmé** pour les clapets ; repli sur perte de sonde
> configurable par circuit ; règles de capacité par réservoir ; écran adaptatif
> 4,3"–5" ; RTC, avertisseur sonore et fonctions optionnelles retirés du
> périmètre initial.
> **Rév. 1** — proposition initiale.

---

## 1. Architecture logicielle

### 1.1 Principe général

Quatre couches strictement séparées, avec une dépendance à sens unique :

```
        ┌──────────────────────────────────────────────┐
        │  UI (PyQt5)  — Accueil, Paramètres, Sim      │   ne connaît AUCUN matériel
        └───────────────▲──────────────────┬───────────┘
        snapshot (signal)│                  │ commandes (file)
        ┌───────────────┴──────────────────▼───────────┐
        │  CORE — logique métier                       │   ne connaît AUCUN matériel
        │  calibration · chauffage · alertes ·         │
        │  historique · état · santé des équipements   │
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

1. `core/` et `ui/` n'importent **jamais** `hal/real/` ni `hal/sim/`. Ils ne
   connaissent que `hal/interfaces.py`. Un test automatique le vérifie.
2. L'instanciation du matériel réel ou simulé se fait **uniquement** dans
   `hal/factory.py`, appelé par le point d'entrée `app.py` (composition root).
3. Passer de la simulation au matériel réel = changer une clé de configuration
   ou un argument de ligne de commande. Aucune autre ligne de code ne change.
4. **L'interface graphique ne détient aucune référence vers le HAL.** Elle ne
   reçoit qu'un `SystemSnapshot` déjà calculé et ne peut qu'empiler des
   commandes dans une file. L'absence d'I/O matérielle dans le thread graphique
   est donc structurelle, pas une question de discipline.

### 1.2 Modèle d'acquisition — un thread par famille de matériel

**Correction rév. 2.** Un thread d'acquisition unique ne garantit pas qu'une
lecture lente ne retarde pas les suivantes : un `try/except` attrape une panne,
il n'interrompt pas une lecture qui dure. La lecture des cinq DS18B20 est le cas
typique (conversion de l'ordre de la seconde par sonde) et ne doit en aucun cas
retarder la logique de chauffage ou l'affichage.

Le modèle retenu est volontairement simple : **un thread par famille de
matériel**, un slot de valeur partagé par famille, et un thread de contrôle qui
ne fait **aucune** I/O.

| Thread | Rôle | Cadence | I/O matérielle |
|---|---|---|---|
| **UI** (principal Qt) | affichage, saisies tactiles | 2 Hz | **non** (aucune référence au HAL) |
| `temp_worker` | 5 × DS18B20 sur 1-Wire | période 10 s | oui — lente |
| `level_worker` | 3 voies de niveau | période 2 s | oui — rapide |
| `battery_worker` | SmartShunt VE.Direct (série) | lecture au fil de l'eau | oui — bloquante, avec délai d'expiration |
| `valve_worker` | exécution des ordres de clapets | sur file | oui |
| `control_worker` | commandes, chauffage, alertes, snapshot | 1 Hz | **non** |
| `display_worker` | veille de l'écran (rév. 8) | 1 Hz, réveil immédiat sur événement | oui — commande externe ou écriture sysfs |
| `history_worker` | écriture SQLite par lots | selon période | non (disque uniquement) |

Sept threads en fonctionnement normal, huit si l'historique est activé. Le thread
d'historique **n'est pas créé** quand l'historique est désactivé.

`display_worker` existe pour une seule raison : éteindre un écran est une
entrée-sortie, et une entrée-sortie n'a rien à faire dans le thread graphique.
Il attend un événement plutôt qu'une échéance, de sorte qu'un réveil demandé par
un doigt est traité tout de suite. **Il ne suspend aucun autre thread** : quand
l'écran dort, tous les autres continuent exactement comme avant.

#### Les trois garanties demandées

**a) Délai d'expiration sur toute I/O matérielle.**
Le contrat de `hal/interfaces.py` impose que **toute méthode de lecture ou de
commande rende la main avant `timeout_s`, ou lève `HardwareTimeout`**. Chaque
pilote reçoit son délai à la construction, depuis la configuration :

* liaison série VE.Direct → délai natif de la bibliothèque série ;
* lecture 1-Wire → lecture avec échéance, abandon et `HardwareTimeout` au-delà ;
* convertisseur de niveaux et pilotes de clapets → délai imposé de la même
  façon dès que le matériel sera choisi (**MATERIEL À INTEGRER PLUS TARD**, mais
  le contrat, lui, est déjà figé).

**b) Aucune lecture lente ne peut bloquer une autre acquisition.**
Les familles sont sur des threads distincts : une lecture 1-Wire qui dure quatre
secondes n'a aucun effet sur les niveaux, la batterie, le chauffage ou
l'affichage. Le `control_worker` ne lit jamais un capteur : il lit des
**valeurs déjà en mémoire** dans les slots, opération immédiate et sans verrou
long.

```python
class LatestValue:                 # un par famille de matériel
    def set(self, value) -> None            # écrit par le worker
    def get(self) -> tuple[Any | None, float]   # (valeur, âge en secondes) — lecture immédiate
    def mark_fault(self, reason: str) -> None
```

**c) Aucune I/O matérielle dans le thread graphique.**
Garanti par construction (règle 4 du §1.1) : l'UI n'a pas d'objet HAL à sa
disposition. Un appui sur `OUVRIR` empile une commande ; c'est le
`valve_worker` qui l'exécute, jamais Qt.

#### Chien de garde et limite assumée

Un thread Python bloqué dans un appel système **ne peut pas être tué de
force** : aucune bibliothèque ne change cela. L'architecture le reconnaît et le
contient au lieu de prétendre l'éviter :

```python
class HardwareWorker(threading.Thread):
    def __init__(self, name, read_fn, period_s, timeout_s, watchdog_factor, slot): ...
    def run(self) -> None: ...          # boucle : lecture sous échéance → slot.set()
                                        # exception → slot.mark_fault(), sans journal répété
    def health(self) -> WorkerHealth: ...   # dernier succès, échecs consécutifs, bloqué ou non
    def request_stop(self) -> None: ...

class WorkerSupervisor:
    def register(self, worker: HardwareWorker) -> None: ...
    def check(self, now: float) -> list[WorkerHealth]: ...   # appelé à chaque tick de contrôle
    def restart_if_stuck(self, worker) -> None: ...
```

Comportement en cas de blocage durable d'un worker (au-delà de
`watchdog_factor × period_s`, facteur 3 par défaut) :

1. les valeurs de cette famille passent en `STALE` puis `FAULT` ;
2. une **alerte technique** est levée (« Sondes de température ne répondent
   plus ») ;
3. le superviseur crée un worker de remplacement ; le thread bloqué est un
   thread *daemon*, il est abandonné et n'empêchera jamais l'arrêt du
   programme ;
4. un seul remplaçant à la fois, avec temporisation croissante plafonnée : pas
   de création de threads en rafale ;
5. **le reste de l'application continue normalement** — c'est exactement le
   comportement recherché.

*Réserve, non implémentée à ce stade :* si un pilote réel se révélait
réellement impossible à interrompre (blocage définitif et répété), la famille
concernée serait déplacée dans un **sous-processus**, tuable, derrière la même
interface. Aucun autre module ne changerait. Cette option est notée ici pour
mémoire ; elle n'est pas retenue tant qu'aucun matériel ne la justifie.

### 1.3 Machine à états et sécurité

* Chaque grandeur portée par le snapshot possède un **statut** :
  `OK` · `STALE` (donnée trop vieille) · `FAULT` (erreur de lecture) ·
  `ABSENT` (capteur non configuré / non détecté).
* L'UI affiche `--` pour `ABSENT`/`STALE` et `Erreur capteur` pour `FAULT`.
* La logique de chauffage n'applique jamais les seuils sur une température qui
  n'est pas `OK` : elle applique le **repli configuré pour ce circuit**
  (voir §5 et §10, R-07) et lève une alerte technique.

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
│   ├── HARDWARE_TODO.md               # points « MATERIEL À INTEGRER PLUS TARD »
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
│   ├── 99-vedirect.rules              # règle udev : nom stable pour l'interface VE.Direct/USB
│   └── kiosk.md                       # notes plein écran / masquage curseur
│
├── src/vanmonitor/
│   ├── __init__.py
│   ├── __main__.py                    # python -m vanmonitor
│   ├── cli.py                         # arguments : --sim, --windowed, --config
│   ├── app.py                         # COMPOSITION ROOT : config + HAL + workers + core + UI
│   ├── constants.py                   # énumérations
│   ├── models.py                      # dataclasses immuables du snapshot
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── defaults.py                # dictionnaire des valeurs par défaut
│   │   ├── schema.py                  # validation + migration de version
│   │   └── store.py                   # ConfigStore : écriture atomique différée
│   │
│   ├── hal/
│   │   ├── __init__.py
│   │   ├── interfaces.py              # TemperatureSensor, LevelSensor, ADCInterface,
│   │   │                              # SmartShuntInterface, ValveDriver, DisplayPower
│   │   │                              # + contrat de délai
│   │   ├── factory.py                 # build_hal(config, simulation: bool) -> HalBundle
│   │   ├── real/
│   │   │   ├── __init__.py
│   │   │   ├── ds18b20.py             # 1-Wire via /sys/bus/w1/devices, lecture sous échéance
│   │   │   ├── adc_level.py           # MATERIEL À INTEGRER PLUS TARD (convertisseur non choisi)
│   │   │   ├── smartshunt_vedirect.py # VE.Direct filaire → interface USB → port série
│   │   │   ├── vedirect_parser.py     # décodage des trames, séparé de la liaison (testable seul)
│   │   │   ├── valve_driver.py        # MATERIEL À INTEGRER PLUS TARD (actionneur non choisi)
│   │   │   └── display_power.py       # extinction de l'écran : vcgencmd / xset / bl_power (rév. 8)
│   │   └── sim/
│   │       ├── __init__.py
│   │       ├── sim_state.py           # état simulé partagé, piloté par le panneau de simulation
│   │       ├── mock_temperature.py    # MockTemperatureSensor
│   │       ├── mock_level.py          # MockLevelSensor
│   │       ├── mock_smartshunt.py     # MockSmartShuntInterface
│   │       ├── mock_valve.py          # MockValveDriver (avec et sans retour de position)
│   │       └── mock_display_power.py  # écran simulé, capable de refuser de s'éteindre (rév. 8)
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── state.py                   # StateStore + LatestValue
│   │   ├── workers.py                 # HardwareWorker + WorkerSupervisor + ValveWorker
│   │   ├── acquisition.py             # AcquisitionService (ajouté rév. 4, voir §13)
│   │   ├── control_loop.py            # ControlWorker : tick 1 s, aucune I/O matérielle
│   │   ├── commands.py                # CommandBus + dataclasses de commandes
│   │   ├── health.py                  # fraîcheur, fautes, anti-rebond
│   │   ├── calibration.py             # CalibrationTable (interpolation multipoints)
│   │   ├── filters.py                 # filtre médian + moyenne exponentielle
│   │   ├── temperature_service.py     # TemperatureService
│   │   ├── tank_service.py            # TankService (eau propre, eaux grises, gasoil)
│   │   ├── battery_service.py         # BatteryService (SmartShunt)
│   │   ├── heating.py                 # HeatingCircuit + HeatingController (hystérésis)
│   │   ├── alerts.py                  # AlertEngine + règles
│   │   ├── display.py                 # DisplayController + DisplayWorker (veille, rév. 8)
│   │   └── history.py                 # HistoryRecorder (SQLite, désactivé par défaut)
│   │
│   ├── ui/
│   │   ├── __init__.py
│   │   ├── main_window.py             # QStackedWidget : Accueil / Paramètres
│   │   ├── theme.py                   # échelle typographique calculée depuis la taille réelle
│   │   ├── style.qss                  # feuille de style sombre
│   │   ├── layout_profile.py          # profil compact / standard choisi à l'exécution
│   │   ├── home_page.py
│   │   ├── settings_page.py           # rail latéral + pages de réglages
│   │   ├── settings/
│   │   │   ├── heating_settings.py
│   │   │   ├── alerts_settings.py
│   │   │   ├── calibration_settings.py
│   │   │   ├── sensors_settings.py    # association des identifiants DS18B20
│   │   │   ├── display_settings.py    # veille de l'écran (rév. 8)
│   │   │   └── history_settings.py
│   │   ├── widgets/
│   │   │   ├── tile.py                # tuile de mesure (grand chiffre + unité)
│   │   │   ├── bar_gauge.py           # jauge horizontale
│   │   │   ├── temperature_list.py
│   │   │   ├── circuit_row.py         # ligne chauffage : ordre commandé vs position confirmée
│   │   │   ├── alert_bar.py
│   │   │   ├── numeric_keypad.py      # pavé numérique tactile
│   │   │   └── touch_controls.py      # boutons/bascules dimensionnés en millimètres
│   │   ├── wake_guard.py              # le premier toucher réveille, et rien d'autre (rév. 8)
│   │   ├── snapshot_text.py           # rendu texte sans Qt (ajouté rév. 4, voir §13)
│   │   └── sim_panel.py               # fenêtre de simulation
│   │
│   └── util/
│       ├── __init__.py
│       ├── logging_setup.py           # logs vers journald, niveau configurable
│       ├── ratelimit.py               # anti-spam de logs (dédoublonnage par message)
│       └── timebase.py                # horloge monotone pour la logique
│
└── tests/
    ├── test_sim_hal.py                # mocks : pannes, clapets avec/sans retour   [étape 2]
    ├── test_workers.py                # délai, worker bloqué, redémarrage          [étape 2]
    ├── test_acquisition.py            # panne d'un capteur → le reste continue     [étape 2]
    ├── test_config_store.py           # écriture atomique, fichier corrompu        [étape 2]
    ├── test_imports.py                # core/ et ui/ n'importent pas hal/real|sim  [étape 2]
    ├── test_sim_panel.py              # panneau de simulation                      [étape 2]
    ├── test_calibration.py            # interpolation, hors plage, capacité déduite [étape 5]
    ├── test_heating.py                # hystérésis, anti-cyclage, repli par circuit [étape 7]
    └── test_alerts.py                 # seuils, réarmement, alertes techniques      [étape 8]
```

---

## 3. Bibliothèques proposées

### 3.1 Retenues

| Bibliothèque | Usage | Justification |
|---|---|---|
| **Python 3.11** (fourni par Raspberry Pi OS Bookworm) | — | pas de compilation, `dataclasses`, typage |
| **PyQt5** (`apt install python3-pyqt5`) | interface graphique | **accepté (rév. 2)**. Paquet système testé et stable sur Raspberry Pi OS, aucun problème d'architecture ARM, plein écran et tactile natifs, styles QSS proches du CSS. Fonctionne à l'identique sur PC. |
| **pyserial** | liaison SmartShunt | **confirmée (rév. 2)** : la liaison VE.Direct filaire passe par une interface VE.Direct/USB, qui se présente comme un port série. Délai d'expiration natif sur la lecture. |
| **sqlite3** (bibliothèque standard) | historique | zéro dépendance, fichier unique |
| **json** (bibliothèque standard) | configuration | lisible, éditable à la main en cas de dépannage |
| **logging** (bibliothèque standard) | journalisation | sortie vers journald, aucune écriture SD dédiée |
| **pytest** | tests | développement uniquement |

Le décodage des trames VE.Direct est écrit dans le projet
(`hal/real/vedirect_parser.py`), séparé de la liaison série : il se teste sans
matériel, à partir de trames enregistrées. Aucune bibliothèque tierce Victron
n'est nécessaire.

### 3.2 Ajoutées seulement quand le matériel sera choisi

| Bibliothèque | Condition |
|---|---|
| bibliothèque du convertisseur analogique-numérique | dépend du modèle choisi — **MATERIEL À INTEGRER PLUS TARD** |
| `gpiozero` / `lgpio` | seulement si les actionneurs de clapets sont pilotés par GPIO — **MATERIEL À INTEGRER PLUS TARD** |

Ces dépendances seront **optionnelles** : import différé, à l'intérieur du
module `hal/real/` concerné. Leur absence ne doit pas empêcher l'application de
démarrer.

**Retirée en rév. 2 :** toute bibliothèque Bluetooth. Le Bluetooth n'est pas
retenu pour le SmartShunt.

### 3.3 Écartées volontairement

* **Aucun serveur web, aucun navigateur** (Flask/Chromium) : consommation
  mémoire et complexité inutiles, démarrage plus lent.
* **Aucune base de données autre que SQLite.**
* **Aucun broker de messages** (MQTT) : tout est dans un seul processus.
* **PySide6** : alternative en réserve si la licence GPL de PyQt5 devenait
  gênante. L'architecture serait identique.

---

## 4. Structure des classes principales

Signatures uniquement — aucune implémentation à ce stade.

### 4.1 Énumérations et modèles (`constants.py`, `models.py`)

```python
class Status(Enum):          OK, STALE, FAULT, ABSENT
class HeatingMode(Enum):     AUTO, MANUEL
class ZoneId(Enum):          LOCAL_BATTERIE, LOCAL_EAU, COFFRE, CABINE, CELLULE
class CircuitId(Enum):       LOCAL_EAU, LOCAL_BATTERIE, CABINE
class TankId(Enum):          EAU_PROPRE, EAUX_GRISES, GASOIL
class AlertLevel(Enum):      INFO, WARN, CRITIQUE
class SensorLossFallback(Enum):  OPEN, CLOSE, HOLD

# --- clapets : trois notions distinctes, jamais confondues (rév. 2) ---
class ValveCommand(Enum):    OPEN, CLOSE, STOP, NONE      # ce que le logiciel a demandé
class ConfirmedState(Enum):  OUVERT, FERME, INCONNU       # ce que le matériel confirme réellement
class ValveState(Enum):      OUVERT, FERME, OUVERTURE, FERMETURE, ERREUR, INCONNU  # état affiché

# --- veille de l'écran (rév. 8) : même distinction ordre / état réel ---
class DisplayState(Enum):    ON, OFF, INCONNU
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
    litres: float | None       # None pour les eaux grises (calibrées en %)
    percent: float | None      # None tant que la capacité n'est pas connue
    raw: float | None
    status: Status
    out_of_range: bool
    calibrated: bool
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
    label: str                     # "Local eau" — jamais "Circuit 1"
    mode: HeatingMode
    zone: ZoneId
    temperature_c: float | None

    # --- état des clapets, rév. 2 ---
    commanded: ValveCommand        # toujours connu : c'est notre propre ordre
    confirmed: ConfirmedState      # INCONNU si le matériel ne renvoie pas de position
    feedback_available: bool       # le pilote fournit-il un retour de position ?
    display_state: ValveState      # état à afficher, dérivé des trois champs ci-dessus
    state_is_certain: bool         # False → l'UI nomme l'ordre, jamais une position
    commanded_since: float
    transition_deadline: float | None

    # --- seuils et repli ---
    open_below_c: float | None     # None = seuil non défini → mode AUTO impossible
    close_above_c: float | None
    thresholds_defined: bool
    on_sensor_loss: SensorLossFallback
    fallback_active: bool          # repli en cours faute de température fiable

    fault: bool
    fault_reason: str | None

@dataclass(frozen=True)
class Alert:
    key: str                   # "eau_propre_basse"
    level: AlertLevel
    message: str               # "Eau propre 18 %"
    active_since: float

@dataclass(frozen=True)
class DisplayStatus:               # rév. 8
    asleep: bool                   # ce que le programme a commandé et cru obtenir
    enabled: bool
    available: bool                # False = aucune méthode d'extinction sur cette machine
    delay_s: float
    idle_s: float
    method: str                    # "vcgencmd", "backlight (rpi_backlight)", …
    last_error: str | None         # dernier échec d'extinction ou de rallumage

@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: float
    temperatures: dict[ZoneId, TemperatureReading]
    tanks: dict[TankId, TankReading]
    battery: BatteryReading
    circuits: dict[CircuitId, CircuitStatus]
    alerts: tuple[Alert, ...]
    display: DisplayStatus | None  # rév. 8 ; None quand la veille n'est pas gérée
    simulation: bool
```

### 4.2 Interfaces matérielles (`hal/interfaces.py`)

**Contrat commun à tous les pilotes :** toute méthode ci-dessous rend la main
avant le `timeout_s` reçu à la construction, ou lève `HardwareTimeout`. Aucune
méthode ne boucle en attendant un matériel absent.

```python
class HardwareError(Exception): ...
class HardwareTimeout(HardwareError): ...
class SensorError(HardwareError): ...
class LinkError(HardwareError): ...
class ValveError(HardwareError): ...


class TemperatureSensor(ABC):
    @abstractmethod
    def read_celsius(self) -> float: ...        # lève SensorError / HardwareTimeout
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
    """
    Rév. 2 — le pilote distingue explicitement ce qui a été COMMANDÉ
    de ce qui est CONFIRMÉ par le matériel.
    """
    @abstractmethod
    def open(self) -> None: ...
    @abstractmethod
    def close(self) -> None: ...
    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def get_commanded_state(self) -> ValveCommand: ...
        # dernier ordre effectivement transmis au matériel — toujours connu

    @abstractmethod
    def has_position_feedback(self) -> bool: ...
        # False tant que le matériel choisi ne fournit pas de retour de position

    @abstractmethod
    def get_confirmed_state(self) -> ConfirmedState: ...
        # OUVERT/FERME UNIQUEMENT si une position réelle est lue.
        # Un pilote sans retour de position renvoie TOUJOURS INCONNU.
        # Il est interdit de renvoyer ici une valeur déduite d'un ordre.

    @abstractmethod
    def get_state(self) -> ValveState: ...
        # vue synthétique destinée à l'affichage : confirmée si elle existe,
        # sinon dérivée de la commande — et signalée comme non certaine
        # via CircuitStatus.state_is_certain

    @abstractmethod
    def has_fault(self) -> bool: ...


class DisplayPower(ABC):
    """Extinction et rallumage de l'affichage (rév. 8).

    Seul l'écran est concerné : ni le Raspberry, ni les threads, ni le
    programme. Comme pour les clapets, l'ordre donné et l'état réel sont
    distincts — certaines méthodes ne savent pas relire la dalle et renvoient
    alors INCONNU, ce qui n'est pas une panne.
    """
    @abstractmethod
    def sleep(self) -> None: ...            # lève HardwareError si la méthode échoue
    @abstractmethod
    def wake(self) -> None: ...
    @abstractmethod
    def state(self) -> DisplayState: ...    # ON / OFF / INCONNU
    @abstractmethod
    def is_available(self) -> bool: ...     # False = veille inopérante, et annoncée comme telle
    @abstractmethod
    def describe(self) -> str: ...          # méthode retenue, affichée dans les Paramètres
```

### 4.3 Acquisition et supervision (`core/workers.py`, `core/state.py`)

```python
@dataclass(frozen=True)
class WorkerHealth:
    name: str
    last_success: float | None
    consecutive_failures: int
    stuck: bool
    restarts: int

class LatestValue:
    def set(self, value) -> None: ...
    def get(self) -> tuple[Any | None, float]: ...      # (valeur, âge) — immédiat
    def mark_fault(self, reason: str) -> None: ...

class HardwareWorker(threading.Thread):
    def __init__(self, name, read_fn, period_s, timeout_s, watchdog_factor, slot): ...
    def run(self) -> None: ...
    def health(self) -> WorkerHealth: ...
    def request_stop(self) -> None: ...

class WorkerSupervisor:
    def register(self, worker: HardwareWorker) -> None: ...
    def check(self, now: float) -> list[WorkerHealth]: ...
    def restart_if_stuck(self, worker: HardwareWorker) -> None: ...
    def stop_all(self, timeout_s: float) -> None: ...
```

### 4.4 Calibration (`core/calibration.py`)

```python
@dataclass(frozen=True)
class CalibrationPoint:
    raw: float
    value: float               # litres OU pourcentage, selon l'unité du réservoir

class CalibrationError(ValueError): ...

class CalibrationTable:
    def __init__(self, points, unit: str, capacity_l: float | None): ...
        # unit = "litres"  → convert() rend des litres
        # unit = "percent" → convert() rend directement un pourcentage

    @classmethod
    def from_config(cls, data: dict) -> "CalibrationTable": ...
    def to_config(self) -> dict: ...

    def validate(self) -> None: ...
        # - au moins 2 points
        # - valeurs brutes strictement monotones (croissantes OU décroissantes)
        # - valeurs converties monotones dans le même sens
        # - aucun doublon de valeur brute
        # - unit="percent" : toutes les valeurs dans [0, 100]
        # - unit="litres" + capacité déclarée : toutes les valeurs dans [0, capacité]
        # → lève CalibrationError avec un message affichable à l'écran

    def effective_capacity(self) -> float | None: ...
        # capacité déclarée si elle existe ;
        # sinon, pour unit="litres", la plus grande valeur de la table
        # (le dernier point de calibration correspond au réservoir plein) ;
        # None si la table est vide → pourcentage non calculable

    def convert(self, raw: float) -> tuple[float, bool]: ...   # (valeur, hors_plage)
    def percent(self, raw: float) -> tuple[float | None, bool]: ...
    def is_calibrated(self) -> bool: ...
```

Les points sont **triés à la construction**. Aucune extrapolation : au-delà du
dernier point, la valeur est bornée et `out_of_range` passe à `True`.

### 4.5 Services métier (`core/`)

```python
class TemperatureService:
    def build_readings(self, now: float) -> dict[ZoneId, TemperatureReading]: ...
        # lit les LatestValue remplis par temp_worker — aucune I/O
    def rebind(self) -> None: ...
    def scan_available_sensor_ids(self) -> list[str]: ...

class TankService:
    def build_readings(self, now: float) -> dict[TankId, TankReading]: ...
    def read_raw(self, tank: TankId) -> float | None: ...       # pendant la calibration
    def set_calibration(self, tank: TankId, table: CalibrationTable) -> None: ...

class BatteryService:
    def build_reading(self, now: float) -> BatteryReading: ...
        # battery_worker gère connexion et reconnexion à intervalle croissant plafonné

class HeatingCircuit:
    def tick(self, temperature: TemperatureReading, now: float) -> CircuitStatus: ...
        # décide seulement ; n'effectue AUCUNE I/O — empile un ordre pour valve_worker
    def request_manual(self, action: str) -> None: ...
    def set_mode(self, mode: HeatingMode) -> None: ...          # refuse AUTO si seuils non définis
    def set_thresholds(self, open_below_c: float, close_above_c: float) -> None: ...

class HeatingController:
    def tick(self, temperatures, now) -> dict[CircuitId, CircuitStatus]: ...

class AlertEngine:
    def evaluate(self, snapshot_parts, worker_health) -> tuple[Alert, ...]: ...

class HistoryRecorder:
    def maybe_record(self, snapshot: SystemSnapshot) -> None: ...
    def purge(self) -> None: ...
    def query(self, since: float) -> list[dict]: ...
    def close(self) -> None: ...
    # history.enabled == False : aucun thread créé, aucun fichier ouvert,
    # aucune base créée, aucune écriture, aucune erreur
```

### 4.6 Configuration (`config/store.py`)

```python
class ConfigStore(QObject):
    changed = pyqtSignal(str)     # chemin de la clé modifiée

    def load(self) -> None: ...            # défauts → fichier → validation → migration
    def get(self, path: str, default=None): ...
    def set(self, path: str, value) -> None: ...
    def save_now(self) -> None: ...        # .tmp → fsync → os.replace
    def reset_section(self, path: str) -> None: ...
```

Écriture **différée de 2 secondes** et regroupée. Sauvegarde `config.bak` avant
remplacement ; en cas de fichier corrompu au démarrage, repli sur `.bak` puis
sur les valeurs par défaut, avec alerte technique.

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
    "screen_diagonal_in": 5.0          // Waveshare 5" (rév. 8)
  },

  "display": {                         // veille de l'écran (rév. 8)
    "sleep_enabled": true,
    "sleep_delay_s": 300,              // 5 min
    "sleep_method": "auto"             // auto | vcgencmd | xset | backlight | none
  },

  "workers": {
    "watchdog_factor": 3,
    "restart_backoff_s": [5, 15, 60, 300]
  },

  "temperatures": {
    "poll_period_s": 10,
    "read_timeout_s": 3.0,
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
    "read_timeout_s": 1.0,
    "filter": { "median_window": 5, "ema_alpha": 0.2 },

    "eau_propre": {
      "label": "Eau propre",
      "display": ["litres", "percent"],
      "unit": "litres",
      "capacity_l": null,                 // capacité inconnue : déduite du dernier point de calibration
      "channel": "CH0",                   // MATERIEL À INTEGRER PLUS TARD
      "calibration": { "points": [], "updated_at": null }
    },
    "eaux_grises": {
      "label": "Eaux grises",
      "display": ["percent"],
      "unit": "percent",                  // calibration directement en %, affichage % uniquement
      "capacity_l": null,
      "channel": "CH1",                   // MATERIEL À INTEGRER PLUS TARD
      "calibration": { "points": [], "updated_at": null }
    },
    "gasoil": {
      "label": "Gasoil",
      "display": ["litres", "percent"],
      "unit": "litres",
      "capacity_l": 105.0,                // capacité connue et déclarée
      "channel": "CH2",                   // MATERIEL À INTEGRER PLUS TARD
      "calibration": { "points": [], "updated_at": null }
    }
  },

  "battery": {
    "stale_after_s": 15,
    "read_timeout_s": 2.0,
    "reconnect_backoff_s": [1, 2, 5, 10, 30],
    "show_time_to_go": true,
    "time_to_go_max_valid_min": 6000,
    "link": {
      "type": "vedirect_serial",
      "port": "/dev/serial/by-id/…",      // nom stable via règle udev — à figer à la mise en service
      "baudrate": 19200                   // paramètres série conformes à la documentation VE.Direct,
                                          // à confirmer au premier branchement
    }
  },

  "heating": {
    "control_period_s": 5,
    "min_state_dwell_s": 120,
    "transition_timeout_s": 60,
    "min_threshold_delta_c": 1.0,
    "circuits": {
      "local_eau": {
        "label": "Local eau", "zone": "local_eau", "mode": "auto",
        "open_below_c": 5.0,               // EXEMPLE, modifiable à l'écran
        "close_above_c": 8.0,              // EXEMPLE, modifiable à l'écran
        "on_sensor_loss": "open",
        "driver": { "type": "mock", "params": {} }   // MATERIEL À INTEGRER PLUS TARD
      },
      "local_batterie": {
        "label": "Local batterie", "zone": "local_batterie", "mode": "manuel",
        "open_below_c": null,              // À DÉFINIR — mode AUTO indisponible tant que null
        "close_above_c": null,
        "on_sensor_loss": "open",
        "driver": { "type": "mock", "params": {} }
      },
      "cabine": {
        "label": "Cabine", "zone": "cabine", "mode": "manuel",
        "open_below_c": null,              // À DÉFINIR
        "close_above_c": null,
        "on_sensor_loss": "hold",
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
    "enabled": false,                     // DÉSACTIVÉ PAR DÉFAUT (rév. 2)
    "sample_period_s": 300,               // 5 minutes si activé
    "retention_hours": 24,
    "db_path": "/var/lib/vanmonitor/history.db",
    "batch_size": 10
  },

  "logging": { "level": "INFO", "dedup_window_s": 300 }
}
```

Notes :

* **Les seuils de chauffage ci-dessus sont des valeurs d'exemple, pas des choix
  définitifs.** Seul `local_eau` porte un exemple chiffré (5 / 8 °C).
  `local_batterie` et `cabine` sont à `null` : à définir. Tous restent
  modifiables à l'écran à tout moment.
* Un circuit dont les seuils valent `null` **ne peut pas passer en AUTO** : le
  mode AUTO est refusé et l'écran affiche `SEUILS À DÉFINIR`. Aucune alerte
  n'est levée pour autant — ce n'est pas une panne.
* `workers`, `min_state_dwell_s`, `transition_timeout_s`, `filter`,
  `reconnect_backoff_s`, `read_timeout_s` et `logging` **ne sont pas modifiables
  dans la page Paramètres** (réglages techniques inutiles au conducteur).
* `on_sensor_loss` **est modifiable dans la page Paramètres**, circuit par
  circuit (rév. 3), mais sous confirmation explicite : c'est un réglage de
  sécurité (§8, section CHAUFFAGE).

---

## 6. Communication entre modules

### 6.1 Threads d'acquisition — indépendants les uns des autres

```
   temp_worker      level_worker      battery_worker
   (10 s, 3 s max)  (2 s, 1 s max)    (série, 2 s max)
        │                 │                  │
        ▼                 ▼                  ▼
   LatestValue       LatestValue        LatestValue      ← écriture sous verrou court
        └─────────────────┴──────────────────┘
                          │  lecture immédiate, jamais bloquante
                          ▼
                    control_worker (1 s)
```

### 6.2 Boucle de contrôle (une seconde) — aucune I/O matérielle

```
  ┌── ControlWorker.tick() ────────────────────────────────────────────┐
  │ 1. CommandBus.drain()            → applique les commandes de l'UI  │
  │ 2. WorkerSupervisor.check()      → santé des threads d'acquisition │
  │ 3. TemperatureService.build_readings()   (lecture mémoire)         │
  │ 4. TankService.build_readings()          (lecture mémoire)         │
  │ 5. BatteryService.build_reading()        (lecture mémoire)         │
  │ 6. HeatingController.tick()      → décisions → ordres empilés      │
  │ 7. AlertEngine.evaluate()                                          │
  │ 8. StateStore.publish(SystemSnapshot)                              │
  │ 9. HistoryRecorder.maybe_record()   (sans effet si désactivé)      │
  └────────────────────────────────────────────────────────────────────┘
                              │ signal Qt snapshotReady (QueuedConnection)
                              ▼
                    HomePage.on_snapshot(snapshot)
```

Chaque étape est protégée individuellement : une exception à l'étape 4
n'empêche ni l'étape 6 ni la publication du snapshot.

### 6.3 Chemin d'une commande utilisateur

```
Appui « OUVRIR » sur Cabine (UI, thread Qt)
    → CommandBus.submit(ManualValveCommand(CircuitId.CABINE, "open"))
    → (tick suivant) HeatingCircuit.request_manual("open")
    → ordre empilé pour valve_worker            ← le thread de contrôle n'attend pas
    → valve_worker : ValveDriver.open()          ← I/O sous délai d'expiration
    → commanded = OPEN, confirmed = lu ou INCONNU
    → CircuitStatus → snapshot → UI
```

L'UI **n'affiche jamais un état qu'elle a supposé**. Elle affiche l'état
construit par le thread de contrôle. Tant qu'aucun retour de position ne
confirme la position, il nomme l'ordre transmis (`OUVERTURE COMMANDÉE`) et non
une position (§7.4).

### 6.4 Chemin d'un changement de réglage

```
Modification d'un seuil (UI)
    → ConfigStore.set("heating.circuits.cabine.open_below_c", 11.0)
    → validation immédiate (fermeture ≥ ouverture + 1 °C)
    → signal changed → HeatingCircuit relit ses seuils au tick suivant
    → écriture disque différée et groupée (2 s)
```

### 6.5 Sens des dépendances (import)

```
ui       → core, config, models, constants
core     → hal.interfaces, config, models, constants
hal.real → hal.interfaces           (+ bibliothèque matérielle, import différé)
hal.sim  → hal.interfaces
app      → tout (seul endroit autorisé)
```

Vérifié automatiquement par `tests/test_imports.py`.

---

## 7. Maquette détaillée — écran Accueil

### 7.1 Contrainte d'affichage (rév. 2, écran figé en rév. 8)

L'écran retenu est le **Waveshare 5 pouces HDMI LCD (H) V4** : 800 × 480,
tactile capacitif, image par HDMI, tactile par USB, orientation paysage. C'est
désormais la **configuration de référence** — celle sur laquelle les captures
sont produites et les cibles tactiles vérifiées.

La mise en page reste néanmoins **adaptative**, comme depuis la rév. 2 : rien
n'y est figé à 800 × 480, ce qui permet de mettre au point sur un PC et de
changer de dalle sans réécrire l'interface.

* aucune position ni taille en pixels absolus : uniquement des dispositions Qt
  proportionnelles ;
* échelle typographique unique calculée à l'exécution depuis la hauteur réelle
  de la dalle (`ui/theme.py`) ;
* cibles tactiles définies en millimètres (≥ 9 mm), converties en pixels selon
  la résolution détectée ;
* deux profils de disposition choisis automatiquement (`ui/layout_profile.py`) :
  **standard** (largeur ≥ 720 px) tel que représenté ci-dessous, et **compact**
  (largeur < 720 px) où les quatre tuiles passent sur deux rangées de deux et le
  bloc chauffage se réduit à trois lignes d'une ligne chacune ;
* enveloppe de validation : la disposition doit rester lisible de 480 × 272 à
  1024 × 600.

La maquette ci-dessous est représentée à 800 × 480, la résolution de l'écran
retenu.

### 7.2 Écran

```
┌──────────────────────────────────────────────────────────────────────────┐
│  FOURGON                         14:32                      ● SIM   [⚙]  │
├───────────────────┬───────────────────┬──────────────┬───────────────────┤
│ BATTERIE          │ EAU PROPRE        │ EAUX GRISES  │ GASOIL            │
│                   │                   │              │                   │
│    87 %           │    68 L           │    42 %      │    76 L           │
│  ▇▇▇▇▇▇▇▇▇░       │    68 %           │  ▇▇▇▇░░░░░░  │    72 %           │
│                   │  ▇▇▇▇▇▇▇░░░       │              │  ▇▇▇▇▇▇▇░░░       │
│  13,2 V   -4,2 A  │                   │              │                   │
│  -55 W   -12 Ah   │                   │              │                   │
│  Autonomie 18 h   │                   │              │                   │
├───────────────────┴──────────┬────────┴──────────────┴───────────────────┤
│ TEMPÉRATURES                 │ CHAUFFAGE                                 │
│                              │                                           │
│ Local batterie      12,4 °C  │ Local eau       AUTO   ● OUVERTE          │
│ Local eau            6,1 °C  │ Local batterie  MANU   ○ FERMETURE         │
│                              │                          COMMANDÉE        │
│ Coffre               9,8 °C  │ Cabine          MANU   ◐ OUVERTURE        │
│ Cabine              18,2 °C  │                                           │
│ Cellule                  --  │                                           │
├──────────────────────────────┴───────────────────────────────────────────┤
│  ⚠  Eau propre 18 %   ·   SmartShunt non joignable                  (2)  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 7.3 Détail des blocs

**Bandeau supérieur**
Titre court, heure, pastille `SIM` uniquement en simulation, bouton engrenage
(seule entrée vers Paramètres).

**Quatre tuiles principales**
* Valeur principale très grande, valeurs secondaires en petit.
* Jauge horizontale : verte, orange sous le seuil d'alerte, rouge en alerte.
  Les eaux grises suivent la logique inverse.
* Batterie : courant et puissance **signés** (négatif = décharge). Autonomie
  affichée seulement si le SmartShunt la fournit et qu'elle est plausible ;
  sinon la ligne disparaît (pas de « N/A »).
* **Eau propre** : litres issus de la calibration multipoints ; pourcentage
  calculé sur la capacité **déduite du dernier point de calibration** tant
  qu'aucune capacité n'est déclarée. Avant calibration : `--` pour les deux.
* **Eaux grises** : pourcentage uniquement, issu directement de la calibration.
  Jamais de litres.
* **Gasoil** : litres issus de la calibration, pourcentage calculé sur 105 L.
* Capteur en défaut : valeur `--` en gris, jauge hachurée, sous-titre
  `Erreur capteur`.

**Bloc Températures**
Cinq lignes fixes, toujours dans le même ordre. `--` si absente ou périmée,
`Erreur capteur` si en défaut.

**Bloc Chauffage** — voir §7.4.

**Barre d'alertes**
Aucune alerte → fond neutre, texte gris **« Aucune alerte »**. Sinon, l'alerte
la plus grave et un compteur ; appui pour dérouler la liste. Aucun clignotement.

### 7.4 Affichage de l'état des clapets (rév. 2)

L'écran distingue en permanence **ce qui a été commandé** de **ce qui est
physiquement confirmé**. Le logiciel n'affiche jamais un état physique comme
certain sans retour de position réel.

| Situation | Affichage | Certitude |
|---|---|---|
| Retour de position disponible, position lue | `● OUVERTE` / `○ FERMÉE` — corps de vanne plein | état **confirmé** |
| Pas de retour de position, ordre d'ouverture passé | `◉ OUVERTURE COMMANDÉE` — corps évidé, orange | état **commandé** |
| Pas de retour de position, ordre de fermeture passé | `◉ FERMETURE COMMANDÉE` — corps évidé, orange | état **commandé** |
| Ordre en cours, dans le délai de transition | `◐ OUVERTURE` / `◑ FERMETURE` | transitoire |
| Retour de position attendu mais non obtenu à l'échéance | `✕ ERREUR` + alerte technique | défaut |
| Retour de position contredisant l'ordre | `✕ ERREUR` + alerte technique | défaut |
| Au démarrage, aucun ordre encore passé et pas de retour | `? INCONNU` | inconnu |

Règles associées :

* `state_is_certain = feedback_available and confirmed != INCONNU`. C'est ce
  champ, et lui seul, qui autorise le vocabulaire `OUVERTE` / `FERMÉE` ; le
  widget ne fait aucune supposition de son côté.
* **`OUVERTE` et `FERMÉE` décrivent une position physiquement confirmée, et
  rien d'autre.** Sans confirmation, l'écran nomme l'ordre transmis :
  `OUVERTURE COMMANDÉE` / `FERMETURE COMMANDÉE`. Le mot est écrit en toutes
  lettres : aucun code de couleur seul, aucune icône seule.
* Un `MockValveDriver` doit exister **dans les deux variantes** (avec et sans
  retour de position) pour que le cas « sans retour » soit testé dès l'étape 2.
* Le repli sur perte de sonde affiche en plus la mention `REPLI` sur la ligne du
  circuit concerné.

---

## 8. Maquette détaillée — écran Paramètres

Deux niveaux seulement : rail de sections à gauche, contenu à droite.

```
┌──────────────────────────────────────────────────────────────────────────┐
│ [←] PARAMÈTRES                                                           │
├──────────────┬───────────────────────────────────────────────────────────┤
│ CHAUFFAGE  ▸ │  CHAUFFAGE                                                │
│ ALERTES      │                                                           │
│ CALIBRATION  │  Local eau                            [ AUTO ] [ MANUEL ] │
│ SONDES       │    Ouverture   [   5,0 °C  ]   Fermeture  [   8,0 °C  ]   │
│ ÉCRAN        │    État : OUVERTURE COMMANDÉE  [ OUVRIR ] [ FERMER ]      │
│ HISTORIQUE   │    Repli si sonde perdue  ⚠                               │
│              │      [ OUVRIR ] [ FERMER ] [ MAINTENIR ]                  │
│              │  ───────────────────────────────────────────────────────  │
│              │  Local batterie                       [ AUTO ] [ MANUEL ] │
│              │    Ouverture   [    --     ]   Fermeture  [    --     ]   │
│              │    ⚠ Seuils à définir — mode AUTO indisponible            │
│              │    État : FERMETURE COMMANDÉE  [ OUVRIR ] [ FERMER ]      │
│              │    Repli si sonde perdue  ⚠                               │
│              │      [ OUVRIR ] [ FERMER ] [ MAINTENIR ]                  │
└──────────────┴───────────────────────────────────────────────────────────┘
```

Règles communes :

* Cibles tactiles ≥ 9 mm ; un appui sur un champ numérique ouvre un **pavé
  numérique plein écran** (`−` / `+`, `Annuler` / `Valider`). Aucun clavier
  système.
* Modifications **appliquées immédiatement**, sauvegardées après 2 s. Pas de
  bouton « Enregistrer » global. **Seule exception : le repli sur perte de
  sonde**, qui demande une confirmation explicite (ci-dessous).
* Valeur refusée → bandeau rouge explicite sous le champ, valeur précédente
  conservée.

### Section CHAUFFAGE
Trois blocs nommés `Local eau`, `Local batterie`, `Cabine`. Pour chacun :
bascule AUTO/MANUEL, seuil d'ouverture, seuil de fermeture, état courant avec la
état courant — `OUVERTE` / `FERMÉE` seulement s'il est confirmé, sinon
`OUVERTURE COMMANDÉE` / `FERMETURE COMMANDÉE` —, boutons `OUVRIR` / `FERMER`
grisés en mode AUTO, et le **repli sur perte de sonde**.
Contraintes : `fermeture ≥ ouverture + 1 °C` ; le bouton `AUTO` est désactivé
tant que les deux seuils ne sont pas définis, avec le message
`Seuils à définir — mode AUTO indisponible`.

#### Repli sur perte de sonde — réglage de sécurité (rév. 3)

Chaque circuit dispose d'un sélecteur à trois positions :
`OUVRIR` · `FERMER` · `MAINTENIR`.

Valeurs par défaut, inchangées :

| Circuit | Repli par défaut |
|---|---|
| Local eau | **OUVRIR** |
| Local batterie | **OUVRIR** |
| Cabine | **MAINTENIR** |

Le sélecteur est visuellement distingué des autres réglages : pictogramme
d'avertissement, libellé `Repli si sonde perdue`, et une ligne d'explication
sous le sélecteur rappelant ce que fait le choix actif.

Contrairement à tous les autres réglages, **le changement n'est pas appliqué
immédiatement** : il ouvre une fenêtre de confirmation.

```
  ┌────────────────────────────────────────────────────────────┐
  │  ⚠   RÉGLAGE DE SÉCURITÉ                                   │
  │                                                            │
  │  Local eau — repli si la sonde ne répond plus              │
  │                                                            │
  │      OUVRIR   →   MAINTENIR                                │
  │                                                            │
  │  Si la sonde Local eau cesse de répondre, le circuit       │
  │  restera dans son dernier état au lieu d'être ouvert.      │
  │  Ce choix conditionne la protection contre le gel.         │
  │                                                            │
  │       [ ANNULER ]                    [ CONFIRMER ]         │
  └────────────────────────────────────────────────────────────┘
```

Règles de la confirmation :

* `ANNULER` est le choix par défaut (bouton mis en avant) ; fermer la fenêtre
  sans répondre équivaut à annuler.
* Le texte est composé à partir du circuit et de la valeur visée, et nomme
  explicitement la conséquence :
  * `OUVRIR` → « le circuit sera ouvert » ;
  * `FERMER` → « le circuit sera fermé — **la protection contre le gel ne sera
    plus assurée** » (formulation renforcée : c'est le choix le plus risqué) ;
  * `MAINTENIR` → « le circuit restera dans son dernier état ».
* Tant que la confirmation n'est pas donnée, le sélecteur reste sur l'ancienne
  valeur : aucun état intermédiaire n'est enregistré.
* Après confirmation : application immédiate, écriture différée habituelle
  (2 s), et une ligne de journal indiquant l'ancienne et la nouvelle valeur.
* Le repli reste sans effet tant que la sonde répond. Dans tous les cas, une
  **alerte technique** est levée lorsqu'un repli s'active, quel que soit le
  choix.

### Section ALERTES
```
  Batterie basse         [  20 %  ]
  Eau propre basse       [  20 %  ]
  Gasoil bas             [  20 %  ]
  Eaux grises hautes     [  80 %  ]
  Alertes techniques     [ ● activées ]
  [ Rétablir les valeurs par défaut ]
```

### Section CALIBRATION
Choix du réservoir, puis assistant identique pour les trois, à une colonne près :

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

  Capacité : déduite du dernier point (100 L)      [ déclarer une capacité ]
  Aperçu : brut 0,412 → 20,0 L → 20 %

  [ EFFACER LA TABLE ]                            [ VALIDER ]
```

| Réservoir | Colonne saisie | Capacité | Affichage Accueil |
|---|---|---|---|
| **Eau propre** | litres | inconnue → déduite du plus haut point de calibration ; déclarable à la main plus tard | litres + % |
| **Eaux grises** | **pourcentage** | sans objet | % uniquement |
| **Gasoil** | litres | 105 L, déclarée et non modifiable par erreur | litres + % |

Calibration effectuée réservoir en cours de remplissage : un point par palier
connu, l'ordre de saisie est indifférent (tri automatique). Refus explicite
d'une table non monotone ou de moins de deux points ; la table précédente reste
active tant que la nouvelle est invalide.

### Section SONDES (association DS18B20)
```
  Sondes détectées : 4                     [ RAFRAÎCHIR ]

  Local batterie   [ 28-0316A2B4C5D6  ▾ ]   12,4 °C
  Local eau        [ 28-0316A2B4E7F8  ▾ ]    6,1 °C
  Coffre           [ 28-0316A2C1029A  ▾ ]    9,8 °C
  Cabine           [ 28-0316A2D45B11  ▾ ]   18,2 °C
  Cellule          [ — non associée —  ▾ ]     --

  [ IDENTIFIER ]  ← affiche en direct la température de la sonde sélectionnée
```

Une même sonde ne peut pas être associée à deux zones.

### Section ÉCRAN (rév. 8)
```
  VEILLE DE L'ÉCRAN
  [ Désactivée ] [ 1 min ] [ 5 min ] [ 10 min ] [ 30 min ]     ← 5 min par défaut
  État                          écran allumé  ·  veille dans 4 min 59

  L'écran seul s'éteint. Le Raspberry reste allumé : les températures, les
  niveaux et la batterie continuent d'être lus, le chauffage continue de
  réguler, et les alertes restent actives.

  Le premier appui sur un écran éteint sert uniquement à le rallumer :
  il ne déclenche aucune commande.
```

Un seul contrôle : le délai, `Désactivée` valant « jamais ». Deux réglages
distincts dans le fichier (`display.sleep_enabled`, `display.sleep_delay_s`)
mais **une seule décision** à l'écran, parce que « activer la veille puis
choisir un délai » serait deux gestes pour une seule intention.

La ligne d'état affiche ce que la veille fait réellement — le temps restant,
l'écran endormi, ou l'indisponibilité de la méthode d'extinction sur la machine
en cours. Une veille qui ne fonctionne pas doit le dire, pas se taire.

### Section HISTORIQUE
```
  Historique              [ ○ activé  /  ● désactivé ]     ← désactivé par défaut
  Fréquence d'enregistrement   [  5 min  ]   (1 min · 5 min · 15 min · 30 min)
  Durée de conservation        [  24 h   ]   (6 h · 12 h · 24 h · 48 h)

  Base absente (historique désactivé)
  [ EFFACER L'HISTORIQUE ]
```

Historique **désactivé par défaut**. Tant qu'il l'est : aucun thread créé,
aucun fichier ouvert, aucune base créée. L'activer crée la base ; le désactiver
la referme immédiatement, sans autre conséquence fonctionnelle.

### Non exposé dans Paramètres (volontairement)
Périodes de scrutation, délais d'expiration, chien de garde, filtres,
temporisations de clapets, délais de reconnexion, port série, niveau de
journalisation, chemins de fichiers, mode simulation.

Le repli par circuit, lui, **est modifiable** depuis la section CHAUFFAGE, sous
avertissement et confirmation explicite (rév. 3).

---

## 9. Éléments matériels à intégrer plus tard

| # | Élément | Ce qui manque | Interface logicielle prévue |
|---|---|---|---|
| H-1 | **Capteurs de niveau** (eau propre, eaux grises, gasoil) | technologie et type de signal de sortie non choisis | `LevelSensor.read_raw()` — valeur brute sans unité |
| H-2 | **Convertisseur analogique-numérique** | modèle, bus et nombre de voies non choisis | `ADCInterface.read_channel(channel)` |
| H-3 | **Actionneurs des 3 circuits de chauffage** | type d'actionneur, alimentation, **et surtout : présence ou non d'un retour de position** | `ValveDriver` avec `get_commanded_state()`, `get_confirmed_state()`, `has_position_feedback()` |
| H-5 | **Câblage 1-Wire des DS18B20** | broche utilisée, longueur de bus, résistance de tirage, alimentation | chemin `/sys/bus/w1/devices` (interface noyau standard) |

### H-4 — Liaison SmartShunt : **résolue (rév. 2)**

Chaîne retenue :

```
Victron SmartShunt  →  VE.Direct filaire  →  interface VE.Direct/USB  →  Raspberry Pi (port série)
```

Le Bluetooth n'est pas retenu. Conséquences :

* `pyserial` devient une dépendance **confirmée** ;
* implémentation `hal/real/smartshunt_vedirect.py`, décodage des trames isolé
  dans `hal/real/vedirect_parser.py` (testable sans matériel) ;
* `battery.link.type = "vedirect_serial"` ;
* le port est désigné par un **nom stable** (`/dev/serial/by-id/…`) via une règle
  udev livrée dans `deploy/`, pour ne pas dépendre de l'ordre d'énumération USB ;
* les paramètres série suivent la documentation VE.Direct de Victron et restent
  dans la configuration, à confirmer au premier branchement ;
* seuls restent à préciser : la **référence exacte de l'interface VE.Direct/USB**
  et le nom stable définitif du port. Aucun des deux ne bloque l'étape 2.

### H-6 — Écran tactile : **résolu (rév. 8)**

**Waveshare 5 pouces HDMI LCD (H) V4** — 800 × 480, tactile capacitif, image par
HDMI, tactile par USB, orientation paysage.

Deux conséquences logicielles :

* `general.screen_diagonal_in` passe de `4.3` à **`5.0`** : les cibles tactiles
  sont dimensionnées en millimètres réels et dépendaient donc de cette valeur.
  La mise en page reste adaptative (§7.1) ; rien n'est figé à 800 × 480.
* **L'image et le tactile empruntent deux liaisons distinctes.** Couper la
  sortie HDMI n'éteint pas le contrôleur tactile USB : le doigt continue d'être
  vu par le système, et peut donc rallumer l'écran. C'est ce qui rend la veille
  (§13, étape 4bis) réalisable sans matériel supplémentaire.

Ce qui reste ouvert : **la commande exacte d'extinction**. Elle dépend de la
pile graphique installée (X11 ou KMS) autant que de la dalle, et n'a pas encore
été éprouvée sur ce Waveshare. `hal/real/display_power.py` en essaie donc trois
(`vcgencmd`, `xset dpms`, `/sys/class/backlight`) et le choix peut être imposé
par `display.sleep_method`. Si aucune ne fonctionne, la veille s'annonce
indisponible dans les Paramètres plutôt que d'échouer en silence.


### Retirés du périmètre initial (rév. 2, révisé en rév. 8)

Horloge temps réel, avertisseur sonore et pilotage de luminosité :
**non implémentés**, non prévus dans la configuration, non affichés dans
l'interface. Ils ne font pas partie du besoin initial et pourront être ajoutés
plus tard sans remise en cause de l'architecture.

La **mise en veille de l'écran** figurait dans cette liste jusqu'à la rév. 7 :
elle est demandée et livrée en rév. 8 (§13, étape 4bis). Elle n'éteint que
l'affichage — c'est une extinction de dalle, pas une mise en veille du
Raspberry, et rien de ce que le système surveille ne s'interrompt.

---

## 10. Risques techniques identifiés

| # | Risque | Impact | Mesure prévue |
|---|---|---|---|
| **R-01** | **Usure et corruption de la carte microSD** (coupures d'alimentation brutales) | perte des réglages et calibrations | écriture atomique + copie de secours ; historique désactivé par défaut et écrit par lots ; journaux en mémoire volatile ; racine en lecture seule envisageable avec une seule partition inscriptible |
| **R-02** | **Lecture 1-Wire lente ou bloquante** (conversion de l'ordre de la seconde par sonde) | acquisitions retardées, interface figée si mal conçu | thread dédié `temp_worker` ; lecture sous échéance avec `HardwareTimeout` ; chien de garde et redémarrage du worker ; le thread de contrôle ne lit que la mémoire (§1.2) |
| **R-03** | **Bus 1-Wire long et bruité dans un fourgon** | valeurs erratiques, sondes qui disparaissent | rejet des valeurs hors plage physique, filtre sur les variations brutales, statut `STALE`, ré-détection périodique |
| **R-04** | **Remplacement d'une sonde DS18B20** : l'identifiant change | zone orpheline | page Sondes : détection, réassociation, fonction « Identifier » ; démarrage normal avec une zone non associée |
| **R-05** | **Liaison VE.Direct filaire** : port série qui change de nom, câble ou interface USB débranchée, trames tronquées | valeurs batterie manquantes | nom de port stable par règle udev ; délai d'expiration en lecture ; trames incomplètes ou incohérentes rejetées ; reconnexion à intervalle croissant plafonné ; statut `STALE` puis alerte technique, jamais de boucle de reconnexion permanente |
| **R-06** | **Autonomie restante peu fiable** (valeur extrême, absente) | affichage trompeur | affichée seulement si présente et plausible, sinon la ligne disparaît |
| **R-07** | **Perte d'une sonde utilisée par le chauffage** | risque de gel ou de surchauffe | **résolu (rév. 2)** : repli configuré **par circuit** — Local eau : ouverture ; Local batterie : ouverture ; Cabine : maintien du dernier état. Alerte technique dans les trois cas, mention `REPLI` sur la ligne du circuit |
| **R-08** | **Absence de retour de position sur les clapets** (matériel non choisi) | un état affiché comme certain alors qu'il ne l'est pas | **traité en profondeur (rév. 2)** : séparation `commanded` / `confirmed` / `state_is_certain` ; un pilote sans retour renvoie toujours `INCONNU` en confirmé ; `OUVERTE`/`FERMÉE` sont réservés aux positions confirmées, sinon l'écran écrit `OUVERTURE COMMANDÉE`/`FERMETURE COMMANDÉE` ; interdiction explicite de déduire un état confirmé d'un ordre |
| **R-09** | **Cyclage rapide des clapets** autour d'un seuil | usure mécanique, consommation | hystérésis réelle (deux seuils) **et** durée minimale de maintien d'état (120 s), contrainte `fermeture ≥ ouverture + 1 °C` |
| **R-10** | **Ballottement du carburant et de l'eau en roulant** | valeurs qui sautent | filtre médian glissant puis moyenne exponentielle ; affichage arrondi |
| **R-11** | **Calibration incohérente saisie par l'utilisateur** | conversion aberrante | validation stricte, message explicite, conservation de la table précédente |
| **R-12** | **Réservoir d'eau de forme irrégulière** et **capacité inconnue** | pourcentage faux ou incalculable | capacité déduite du plus haut point de calibration, déclarable plus tard ; `--` tant que la table est vide ; bornage explicite plutôt qu'extrapolation |
| **R-13** | **Heure système fausse** — pas d'Internet, et **aucune horloge temps réel prévue** | horodatage de l'historique incohérent après coupure | toute la logique fonctionne sur horloge **monotone** ; l'historique enregistre le temps écoulé depuis le démarrage en plus de l'heure murale ; l'heure murale est signalée non fiable tant qu'elle n'a pas été réglée |
| **R-14** | **Saturation des journaux** en cas de panne répétée | usure disque, journaux illisibles | dédoublonnage sur 5 minutes puis résumé (« 312 occurrences ») |
| **R-15** | **Plantage de l'application** | perte de l'affichage et de la commande | redémarrage automatique par systemd avec temporisation croissante ; au redémarrage, l'état des clapets est `INCONNU` tant qu'il n'est ni relu ni recommandé — jamais supposé |
| **R-16** | **Thread d'acquisition définitivement bloqué** dans un appel système : Python ne permet pas de le tuer | fuite d'un thread, famille de capteurs perdue | limite assumée et contenue : thread *daemon* abandonné, remplaçant créé avec temporisation croissante, un seul à la fois, alerte technique ; isolation en sous-processus tenue en réserve si un pilote réel le justifie (§1.2) |
| **R-17** | **Divergence entre mode simulé et mode réel** | anomalies découvertes seulement dans le fourgon | les mocks implémentent les mêmes interfaces et simulent les **pannes** : absence, valeur aberrante, lecture qui dure, coupure de liaison, défaut de clapet, **et clapet sans retour de position** |
| **R-18** | **Résolution et modèle d'écran inconnus** | mise en page cassée sur la dalle réelle | **clos (rév. 8)** : Waveshare 5" 800 × 480 retenu (H-6). Les mesures restent en place — aucun pixel absolu, échelle calculée à l'exécution, deux profils, enveloppe 480 × 272 → 1024 × 600 — pour ne pas dépendre d'un modèle précis |
| **R-19** | **Performances graphiques** si le rafraîchissement est trop fréquent | interface saccadée, chauffe | 2 Hz, redessin des seuls éléments modifiés, aucune animation permanente |
| **R-20** | **Consommation du Raspberry en stationnement** | décharge de la batterie auxiliaire | hors périmètre logiciel initial ; à traiter au niveau électrique. La veille d'écran (rév. 8) réduit la consommation de la dalle, mais **pas celle du Raspberry** : elle n'est pas une réponse à ce risque |
| **R-21** | **Méthode d'extinction de l'écran inopérante** sur la pile graphique réellement installée (rév. 8) | veille silencieusement sans effet, ou pire : écran noir qui ne se rallume pas | trois méthodes essayées et choix forçable (`display.sleep_method`) ; l'indisponibilité est **affichée** dans les Paramètres ; un échec d'extinction **ou** de rallumage laisse l'état interne à « allumé », pour ne jamais avaler le toucher suivant ; rallumage systématique à l'arrêt du programme |
| **R-22** | **Le toucher de réveil active ce qui se trouve sous le doigt** (rév. 8) | ouverture d'un clapet ou modification d'un seuil sans intention, sur un écran qu'on ne voyait pas | le geste de réveil entier — appui, déplacements, relâchement — est absorbé par un filtre posé sur l'application (`ui/wake_guard.py`) ; garde-fou de 2 s si le relâchement n'arrive jamais. **Renforcé en rév. 9** : chaque flux d'entrée (tactile natif, souris synthétisée, clavier) est suivi séparément, l'absorption ne s'arrêtant que lorsque tous ceux vus s'ouvrir se sont refermés |
| **R-23** | **Le réveil est asynchrone** : la fin du geste arrive avec l'écran encore marqué endormi (rév. 9) | rouvrir une absorption sans la refermer ferait avaler le premier appui utile de l'utilisateur | le filtre ouvre sa fenêtre d'absorption une seule fois par geste et la referme sur le relâchement, que le réveil ait abouti ou non ; vérifié avec un réveil différé, tel que le thread réel le produit |

---

## 11. Points encore ouverts

Aucun ne bloque l'étape 2.

| # | Point | Quand il faudra trancher |
|---|---|---|
| 1 | **Seuils de chauffage Local batterie et Cabine** | à la mise au point, directement à l'écran. Restent `null` jusque-là, AUTO indisponible pour ces deux circuits |
| 2 | **Capacité du réservoir d'eau propre** | facultatif : elle sera déduite de la calibration. Déclarable à tout moment |
| 3 | **Capteurs de niveau et convertisseur** (H-1, H-2) | étape 11 |
| 4 | **Actionneurs de clapets, avec ou sans retour de position** (H-3) | étape 11. Les deux cas sont déjà couverts par le modèle |
| 5 | ~~**Modèle et résolution d'écran** (H-6)~~ | **tranché en rév. 8** : Waveshare 5" HDMI LCD (H) V4, 800 × 480 |
| 6 | **Référence de l'interface VE.Direct/USB et nom stable du port** | étape 6, au premier branchement |
| 7 | **Méthode d'extinction retenue pour ce Waveshare** (rév. 8) | au premier essai sur le Raspberry. `auto` d'ici là ; `display.sleep_method` permet de la figer sans toucher au code |

---

## 12. Suite du projet

Aucun code applicatif n'est produit à ce stade. Après validation finale de ce
document :

| Étape | Contenu | Livrable |
|---|---|---|
| 2 | Mode simulation | HAL complet + mocks (dont clapet sans retour de position) + workers + panneau de simulation |
| 3 | Interface graphique | Accueil et Paramètres alimentés par la simulation |
| 4 | Températures | `TemperatureService` + association des sondes |
| 5 | Niveaux et calibrations | `CalibrationTable` + assistant de calibration |
| 6 | SmartShunt | `BatteryService` + liaison VE.Direct filaire |
| 7 | Chauffage | `HeatingController` + hystérésis + modes + repli par circuit |
| 8 | Alertes | `AlertEngine` |
| 9 | Historique | `HistoryRecorder`, désactivé par défaut |
| 10 | Paramètres et configuration | persistance complète |
| 11 | Matériel réel | remplacement des mocks, une famille à la fois |
| 12 | Démarrage automatique | systemd, plein écran, redémarrage sur incident |
| 13 | Tests finaux | validation sur banc puis dans le fourgon |

À chaque étape : le mode simulation reste fonctionnel, l'architecture n'est pas
modifiée sans justification, aucune fonction validée n'est supprimée, et les
fichiers modifiés sont fournis **entiers** avec leur emplacement exact.

---

## 13. Journal d'implémentation

### Étape 2 — mode simulation (livrée)

Cinq écarts par rapport au document validé. Aucun ne change l'architecture ;
tous sont signalés ici comme convenu.

| # | Écart | Raison |
|---|---|---|
| **É-1** | **Fichier ajouté** `core/acquisition.py` (`AcquisitionService`) | L'assemblage des threads et des emplacements de valeurs n'avait pas de place attitrée. Le loger dans `workers.py` aurait mélangé le mécanisme générique des threads avec la description de l'installation. |
| **É-2** | **Fichier ajouté** `ui/snapshot_text.py` | Le rendu texte de l'état est partagé entre le panneau de simulation et le mode `--headless`. Sans ce module, le mode sans interface aurait dépendu de PyQt5, ce qui l'aurait rendu inutilisable là où il sert justement. |
| **É-3** | `ConfigStore` **n'hérite plus de `QObject`** ; la notification passe par des fonctions de rappel (`add_listener`) au lieu d'un signal Qt | Le document prévoyait `ConfigStore(QObject)`, ce qui aurait fait dépendre `config/` et `core/` de Qt — donc rendu la logique métier intestable sans interface graphique et sans `QApplication`. L'interface s'abonne exactement de la même façon. |
| **É-4** | **Règle ajoutée** : chaque mesure est datée de son **début**, et une mesure antérieure à celle déjà publiée est refusée (`LatestValue.set(..., measured_at=…)`) | Découvert en écrivant les tests du chien de garde : un thread déclaré bloqué puis remplacé peut se débloquer une minute plus tard et publier ce qu'il avait lu. Sans cette règle, une valeur périmée écrasait une valeur fraîche. C'est le complément indispensable de la limite assumée « on ne peut pas tuer un thread bloqué ». |
| **É-5** | Une sonde **détectée absente** passe en `ABSENT` (`--`) et non en `FAULT` (« Erreur capteur ») | Le document définissait déjà ces deux statuts ; l'implémentation les distingue à la lecture (`is_present()`) au lieu de tout traiter en erreur. Une sonde débranchée n'est pas un capteur en défaut. |

Également précisé à l'implémentation, sans contredire le document :

* les observations de clapets sont transportées comme les autres grandeurs,
  dans un `Sample` — ce qui distingue « actionneur non intégré » de « pas
  encore lu » ;
* `ValveWorker` relit l'état des clapets **avant** d'attendre une commande,
  pour que l'écran les connaisse dès le démarrage ;
* en simulation, une zone dont la sonde n'est pas associée est reliée
  automatiquement à la sonde simulée correspondante, **sans écrire dans la
  configuration** ;
* tous les nombres affichés utilisent la **virgule** décimale.

**Non livré à l'étape 2, conformément au découpage :** écran Accueil et écran
Paramètres (étape 3), calibration (5), services métier (4 à 6), hystérésis (7),
alertes (8), historique (9). Les modules `hal/real/` existent et lèvent
`NotImplementedError` avec un message explicite.

### Étape 3 — interface graphique (livrée)

#### Divergences tranchées entre la capture de référence et le texte

| # | Point | Décision |
|---|---|---|
| **D-1** | Le texte demandait **orange pour les eaux grises** et **vert pour le gasoil** ; la capture de référence montre **gris** pour les eaux grises et **ambre** pour le gasoil | La capture a été suivie, puisque la demande était de la reproduire fidèlement. Les trois couleurs sont regroupées dans `TANK_COLORS` (`ui/theme.py`) : les permuter est une ligne |
| **D-2** | La capture montre **cinq onglets** (Accueil, Chauffage, Niveaux, Températures, Paramètres) ; le texte demande une navigation **Accueil / Paramètres** uniquement, répétée dans les trois échanges | Le texte a été suivi : deux entrées, dans le style de barre de la capture. Les blocs Chauffage, Niveaux et Températures sont déjà sur l'accueil, des onglets dédiés feraient doublon |

#### Écarts d'implémentation

| # | Écart | Raison |
|---|---|---|
| **É-6** | **Fichier ajouté** `core/services.py` regroupant `TemperatureService`, `TankService`, `BatteryService` et `HeatingService` | L'arborescence prévoyait quatre fichiers ; chacun fait une trentaine de lignes et ils partagent le même contrat (instantané d'acquisition → grandeurs affichables). Quatre fichiers auraient coûté plus de navigation qu'ils n'auraient apporté de clarté |
| **É-7** | La section Chauffage des Paramètres affiche **un circuit à la fois**, choisi par un sélecteur | Les empiler imposait de faire défiler la page pour atteindre le repli du troisième circuit — inacceptable pour un réglage de sécurité sur une dalle de 4,3 pouces |
| **É-8** | Cible tactile ramenée à ≈ 38 px au lieu des 9 mm annoncés à l'étape 1 | Sur une dalle de 4,3 pouces en 800 × 480, 9 mm valent environ 75 px : la page Chauffage n'aurait tenu que quatre contrôles. Les cibles font 38 à 46 px (≈ 5 à 6 mm), ce qui reste confortable au doigt ; la barre de navigation, elle, est plus généreuse |
| **É-9** | En simulation, un réservoir non calibré est converti par une **table de démonstration**, et l'écran de calibration l'annonce comme telle | Sans cela l'accueil afficherait `--` partout et la maquette serait invérifiable. La table n'est jamais écrite dans la configuration et n'existe pas sur le matériel réel |
| **É-10** | La régulation **automatique** du chauffage n'est pas active | Elle reste l'étape 7. Un circuit en AUTO conserve son état ; l'écran n'affiche donc rien de faux, et le mode AUTO est refusé tant que les seuils ne sont pas définis |

#### Points d'attention retenus dans l'interface

* un état de clapet non confirmé se distingue **par la forme** (corps de vanne
  évidé) autant que par la couleur, et nomme l'ordre plutôt qu'une position
  (`OUVERTURE COMMANDÉE`) — un œil qui distingue mal les teintes doit pouvoir
  trancher ;
* une sonde débranchée affiche `--`, une sonde en défaut affiche
  `Erreur capteur` : ce sont deux situations différentes ;
* l'autonomie disparaît quand le SmartShunt ne la fournit pas, plutôt que
  d'afficher « N/A » ;
* aucune information technique n'apparaît sur l'écran principal. Le seul témoin
  toléré est la pastille `SIM`, et le panneau de simulation reste une fenêtre
  séparée ;
* l'heure n'est affichée que si elle a été réglée : sans horloge temps réel ni
  Internet, mieux vaut `--:--` qu'une heure fausse.

#### Corrections apportées après relecture

**C-1 — Vocabulaire des clapets.** « OUVERTE » et « FERMÉE » décrivent
désormais **exclusivement une position physique confirmée** par un retour de
position réel. Sans confirmation, l'écran annonce la commande et non une
position :

| Situation | Libellé | Forme |
|---|---|---|
| Position confirmée par le matériel | `OUVERTE` · `FERMÉE` | corps de vanne plein |
| Ordre transmis, rien de confirmé | `OUVERTURE COMMANDÉE` · `FERMETURE COMMANDÉE` | corps évidé, orange |
| Course en cours, constatée | `OUVERTURE` · `FERMETURE` | corps évidé, ambre |
| Aucun ordre, aucun retour | `INCONNU` + « sans retour de position » | corps évidé, gris |
| Défaut d'actionneur | `DÉFAUT` | corps barré, rouge |

La mention « commandé » ajoutée sous l'état a disparu : elle faisait double
emploi avec un libellé qui porte désormais lui-même l'information. Le modèle
`commandé` / `confirmé` de l'étape 1 est inchangé — seule sa formulation à
l'écran l'est. Le rendu texte (`--headless`, panneau de simulation) emploie le
même vocabulaire.

**C-2 — Cibles tactiles dimensionnées en millimètres réels.** Elles ne sont
plus exprimées en pixels mais converties depuis la **taille physique** de la
dalle, via une nouvelle clé `general.screen_diagonal_in` (4,3" par défaut à
l'époque, la borne la plus contraignante de la fourchette annoncée ; **5,0"
depuis la rév. 8**, l'écran étant désormais choisi) :

| Dalle | Résolution | Cible tactile | Navigation |
|---|---|---|---|
| 4,3" | 800 × 480 | 72 px (8,4 mm) | 48 px |
| 5" | 800 × 480 | 66 px (9,0 mm) | 48 px |
| 7" | 1024 × 600 | 60 px (9,0 mm) | 60 px |

L'écart **É-8** de la rév. 5 est donc annulé : la cible de 9 mm est tenue,
bornée à 15 % de la hauteur pour qu'une page ne se réduise pas à deux réglages.
Toutes les commandes citées à la relecture en bénéficient : AUTO/MANUEL,
OUVRIR/FERMER, champs de seuils, choix du repli, pavé numérique, boutons de
confirmation.

Conséquence assumée : **la page Paramètres défile verticalement**. La barre de
défilement est donc rendue visible en permanence et la page se tire au doigt
(défilement cinétique), plutôt que de viser une barre étroite. **L'écran
Accueil, lui, reste entièrement visible sans défilement** — c'est pourquoi la
barre de navigation garde une cible plus mesurée, plafonnée à 10 % de la
hauteur.

**C-3 — Autonomie SmartShunt : vérifiée, et verrouillée par des tests.** Le
comportement était déjà correct ; il est désormais prouvé
(`tests/test_battery_autonomy.py`, 15 tests) :

* une lecture `STALE` ou `FAULT` ne produit **aucune** grandeur affichable —
  ni autonomie, ni état de charge, ni tension. La dernière valeur reçue reste
  dans le `LatestValue` pour le diagnostic, mais elle n'atteint jamais l'écran ;
* la péremption se calcule depuis la dernière lecture **réussie**, pas depuis la
  dernière tentative ;
* une autonomie nulle, négative ou supérieure à `time_to_go_max_valid_min` est
  écartée, sans que le reste de la lecture soit perdu ;
* une autonomie absente fait disparaître la ligne : jamais de « N/A » ;
* de bout en bout, couper la liaison VE.Direct efface l'autonomie de
  l'instantané publié.

L'observation qui a motivé la vérification (17 % d'état de charge et 18 h
d'autonomie) venait bien du shunt simulé, dont l'autonomie était une constante.
Le SmartShunt simulé la **recalcule** maintenant à partir de la charge restante
et du courant, comme le ferait le matériel : le même cas affiche désormais
2 h 20. Il rend « aucune autonomie » en charge ou à courant nul, ce qu'un vrai
shunt annonce comme infini.

### Étape 4 — gestion des températures (livrée)

L'interface validée n'a pas bougé, et le mode simulation reste entier. Ce qui
change est dessous.

#### Pilote DS18B20 réel

`hal/real/ds18b20.py` n'est plus une souche. Le modèle de sonde étant confirmé,
son interface avec le noyau l'est aussi : le module `w1-therm` expose chaque
sonde dans `/sys/bus/w1/devices/<id>/`. **MATERIEL À INTEGRER PLUS TARD reste
vrai pour le câblage seul** (broche, longueur de bus, résistance de tirage,
alimentation : H-5).

Trois précautions qui comptent sur un bus qui traverse un fourgon :

* les **deux formats du noyau** sont lus — `temperature` (millidegrés, noyaux
  récents) et `w1_slave` (deux lignes, noyaux plus anciens) ;
* la **somme de contrôle** de `w1_slave` est vérifiée : sur un bus long et
  secoué, une trame corrompue arrive vraiment, et une valeur fausse vaut moins
  qu'une absence de valeur ;
* la valeur **85,000 °C** est refusée : c'est la valeur d'initialisation du
  registre de la DS18B20, pas une mesure. Une sonde qui la renvoie n'a pas
  converti.

#### Filtrage des valeurs aberrantes (R-03)

`core/filters.py` apporte un `SpikeGuard` : une mesure qui s'écarte de plus de
`temperatures.max_step_c` (12 °C par défaut) de la précédente est **mise en
attente** plutôt que publiée. Si la mesure suivante la confirme, le changement
est réel et les deux passent ; sinon la première est oubliée.

Le compromis est explicite : une trame corrompue ne s'affiche jamais, et un
vrai changement brutal — une porte ouverte en hiver — coûte **un seul cycle**
de retard. Une évolution normale n'est jamais retardée. Pendant l'attente, le
statut ne change pas : ce n'est pas une panne, c'est une trame douteuse.

#### Le bus 1-Wire est vivant

Il est rebalayé à chaque cycle de lecture. L'instantané transporte désormais
`available_sensor_ids` : une sonde débranchée disparaît de la liste, une sonde
branchée y apparaît, sans redémarrage ni bouton à presser.

Une zone associée à une sonde absente du bus le dit : elle affiche `--` avec le
statut `ABSENT` (pas `FAULT` — voir É-5), et la page Sondes marque
l'identifiant « (absente) » sans jamais effacer l'association. Un câble
débranché ne doit pas faire perdre un réglage.

#### Association et identification à chaud

C'est le manque le plus concret que l'étape comblait : **réassocier une sonde
depuis les Paramètres prend maintenant effet immédiatement**. `AcquisitionService`
écoute la configuration, reconstruit la sonde de la zone concernée, remet à
zéro son filtre et invalide la valeur précédente — la température de l'ancienne
sonde n'est jamais attribuée à la nouvelle zone, même une seconde.

Pour identifier physiquement une sonde, il faut pouvoir la lire **avant** de
décider où elle va. Tant que la section Sondes est ouverte, l'acquisition lit
donc aussi les sondes détectées mais associées à aucune zone, et la page les
affiche avec leur température : on en réchauffe une à la main et on regarde
laquelle monte. Cette lecture supplémentaire **s'arrête dès qu'on quitte la
page** — sur un bus 1-Wire, chaque sonde lue en plus l'occupe près d'une
seconde par cycle.

En simulation, une sonde mesure la température de **l'endroit où elle est
posée**, pas de la zone à laquelle le logiciel l'attribue. Associer la sonde du
coffre à la cabine affiche donc la température du coffre : sans cela, une
erreur d'association resterait invisible et la page Sondes ne servirait à rien.

#### Ajouts à la configuration

| Clé | Défaut | Rôle |
|---|---|---|
| `temperatures.max_step_c` | `12.0` | écart maximal admis entre deux lectures d'une même sonde avant mise en attente |
| `general.screen_diagonal_in` | `4.3`, porté à `5.0` en rév. 8 | diagonale physique de la dalle (ajoutée en rév. 6, pour les cibles tactiles) |

#### Ce qui n'a pas changé

Écrans Accueil et Paramètres : aucune modification visuelle, hors la section
Sondes qui affiche désormais ce que l'étape 1 avait prévu (« Sondes
détectées : N ») et le bloc des sondes libres pendant l'identification. Le
panneau de simulation, les alertes, la calibration, le chauffage : inchangés.

---

### Étape 4bis — écran de référence et veille de l'affichage (livrée)

Deux demandes intercalées avant l'étape 5 : figer l'écran de référence, et
mettre l'affichage en veille sans rien endormir d'autre.

#### Ce que la veille éteint, et ce qu'elle ne touche pas

C'est la seule chose qui compte vraiment ici. Un tableau de bord qui cesserait
de surveiller pendant qu'il dort ne serait plus un tableau de bord.

| Éteint | Continue |
|---|---|
| l'affichage (sortie HDMI ou rétroéclairage) | l'application PyQt5, jamais fermée |
| | les six threads d'acquisition, aucun suspendu |
| | la lecture des cinq sondes, des trois niveaux, du SmartShunt |
| | la boucle de contrôle à 1 Hz et l'assemblage de l'instantané |
| | l'évaluation des alertes |
| | la chaîne du chauffage : file de commandes, clapets, relecture de position |
| | Linux, qui n'est ni mis en veille ni suspendu |

Aucun appel à `quit()`, `close()`, `systemctl suspend` ou équivalent n'existe
dans le code de la veille. L'interface graphique continue même de se rafraîchir
derrière l'écran noir : la rallumer n'a donc aucun temps de reconstruction.

#### Découpage

| Fichier | Rôle |
|---|---|
| `hal/interfaces.py` → `DisplayPower` | contrat abstrait : `sleep()`, `wake()`, `state()`, `is_available()`, `describe()` |
| `hal/real/display_power.py` | trois méthodes réelles (`vcgencmd`, `xset dpms`, `bl_power`) + `NullDisplayPower` quand aucune ne convient |
| `hal/sim/mock_display_power.py` | écran simulé, capable de **refuser** de s'éteindre |
| `core/display.py` | `DisplayController` (la règle) et `DisplayWorker` (le thread qui l'applique) |
| `ui/wake_guard.py` | filtre d'événements : le premier toucher réveille, et rien d'autre |
| `ui/settings/display_settings.py` | la section ÉCRAN des Paramètres |

Le découpage suit celui du reste du projet : `core/` décide, `hal/` exécute,
`ui/` ne fait que demander. `core/display.py` ne connaît que `hal.interfaces`,
et l'extinction elle-même — une commande externe, une écriture sysfs — a lieu
dans un thread dédié, jamais dans le thread graphique.

Le réveil reste immédiat parce que ce thread **attend un événement** plutôt
qu'une échéance : le doigt le débloque, il n'attend pas la fin de sa période.

#### Le premier toucher ne fait que réveiller

Sur un écran noir, on ne voit pas ce qui se trouve sous le doigt. Si le premier
contact activait le bouton en dessous, on ouvrirait un clapet ou on changerait
un seuil sans l'avoir voulu.

`WakeGuard` est donc posé sur le `QApplication` — pas sur une fenêtre : ainsi
aucun événement ne peut le contourner. Il absorbe le **geste entier** (appui,
déplacements, relâchement), avec un garde-fou de 2 s si le relâchement n'arrive
jamais. Dès le doigt levé, l'interface est normalement utilisable : il n'y a
aucun délai supplémentaire, et le deuxième appui agit.

Le même filtre sert à repousser la veille : toute interaction, réveil compris,
remet le compteur d'inactivité à zéro.

#### Trois flux d'entrée, une seule règle (vérifié en rév. 9)

Une dalle tactile USB n'arrive pas toujours à Qt de la même façon, et le
programme ne choisit pas : cela dépend du greffon de plateforme, de la pile
d'entrée et de la configuration du système.

| Flux | Ce que le filtre reçoit |
|---|---|
| **tactile natif** | `TouchBegin` · `TouchUpdate` · `TouchEnd` (ou `TouchCancel`) |
| **souris synthétisée** | la dalle est exposée comme un pointeur : `MouseButtonPress` · `MouseMove` · `MouseButtonRelease` |
| **les deux à la fois** | Qt peut doubler un événement tactile non consommé d'un événement souris synthétisé |

Le filtre ne cherche pas à deviner lequel est utilisé : il **suit chaque flux
séparément** (souris, tactile, clavier) et n'a fini d'absorber que lorsque tous
ceux qu'il a vus s'ouvrir se sont refermés. Une molette ou un déplacement, qui
n'ouvrent aucun geste, sont absorbés sans prolonger l'absorption.

Deux défauts ont été trouvés par cette vérification et corrigés :

* **le relâchement du troisième flux passait.** Refermer le geste sur le
  premier relâchement venu laissait passer le relâchement souris arrivant après
  le `TouchEnd`. Inoffensif sur un `QPushButton` — Qt ignore un relâchement sans
  appui — mais la règle demandée est qu'**aucun** événement n'atteigne le
  widget, pas qu'il soit sans effet ;
* **le deuxième appui pouvait être avalé.** Le réveil ayant lieu dans un autre
  thread, la fin du geste de réveil arrive avec l'écran encore marqué endormi.
  L'ancienne version rouvrait alors une absorption sans jamais la refermer, et
  mangeait l'appui suivant — le premier appui utile de l'utilisateur. Ce défaut
  n'apparaissait qu'avec un réveil **asynchrone**, c'est-à-dire dans la
  configuration réelle ; les tests de l'étape 4bis, qui réveillaient de façon
  synchrone, le masquaient.

#### Une veille qui échoue le dit

La méthode d'extinction dépend de la pile graphique installée, pas seulement de
la dalle (R-21). Deux règles en découlent :

* si `sleep()` échoue, l'état interne **reste « allumé »**. Se croire endormi
  ferait avaler le toucher suivant pour rien ;
* si `wake()` échoue, même chose, pour la même raison.

Dans les deux cas le message d'erreur remonte jusqu'à la section ÉCRAN. Une
veille indisponible s'affiche comme telle ; elle n'échoue jamais en silence.
À l'arrêt du programme, l'écran est systématiquement rallumé : on ne laisse pas
un noir derrière soi.

#### Écarts d'implémentation

| # | Écart | Raison |
|---|---|---|
| **É-11** | **Une sixième entrée « Écran » dans le rail des Paramètres** — seule modification visuelle de cette étape | Aucune des cinq sections existantes (Chauffage, Alertes, Calibration, Sondes, Historique) ne pouvait accueillir honnêtement un réglage d'affichage. Le rail tient : 6 × 48 px + 5 × 4 px = 308 px pour ≈ 319 px disponibles en 800 × 480. Les écrans Accueil et Paramètres sont inchangés par ailleurs |
| **É-12** | **Un seul contrôle pour deux clés** (`sleep_enabled`, `sleep_delay_s`) | « Activer la veille » puis « choisir un délai » sont deux gestes pour une seule intention. `Désactivée` écrit `sleep_enabled = false` et **conserve** le délai précédent, qui est retrouvé tel quel à la réactivation |
| **É-13** | **La commande d'extinction n'est pas figée** | Elle n'a pas été éprouvée sur ce Waveshare, et l'inventer serait contraire à la règle du projet. Trois méthodes sont essayées dans l'ordre, `display.sleep_method` permet d'en imposer une, et l'échec est visible (point ouvert n° 7) |
| **É-14** | **Une alerte ne rallume pas l'écran** | Cela n'a pas été demandé, et un rallumage automatique en pleine nuit serait une décision à prendre, pas à supposer. Les alertes continuent d'être évaluées et sont là, affichées, dès le réveil |

#### Ajouts à la configuration

| Clé | Défaut | Rôle |
|---|---|---|
| `display.sleep_enabled` | `true` | veille active |
| `display.sleep_delay_s` | `300` | délai d'inactivité, parmi 60 / 300 / 600 / 1800 |
| `display.sleep_method` | `"auto"` | `auto`, `vcgencmd`, `xset`, `backlight` ou `none` |
| `general.screen_diagonal_in` | `5.0` (était `4.3`) | l'écran est choisi (H-6) |

#### Vérifications ajoutées

`tests/test_display_sleep.py` — 18 tests, dont les neuf demandés à l'étape 4bis :
déclenchement après le délai configuré ; aucune veille quand elle est
désactivée ; remise à zéro du compteur à chaque interaction ; premier toucher =
réveil seul ; premier toucher ne déclenchant aucune commande ; acquisitions,
chaîne du chauffage et alertes qui continuent écran éteint ; délai des
Paramètres réellement appliqué. S'y ajoutent la désactivation pendant que
l'écran dort, une méthode d'extinction en panne, une méthode indisponible.

Cinq tests couvrent le geste de réveil lui-même (rév. 9) : les trois flux
d'entrée ci-dessus, un geste dont le relâchement n'arrive jamais, et l'appui qui
suit un réveil **asynchrone**. Ces cinq-là vérifient deux choses à chaque fois —
le réveil est demandé, et **aucun événement n'atteint le widget**.

Une limite assumée : `QApplication.notify` aiguille un `QTouchEvent` d'après ses
**points de contact**, que PyQt5 ne sait pas construire. Un événement tactile
émis depuis un test n'atteint donc jamais la boucle de distribution. Les tests
des flux appellent par conséquent `eventFilter` comme Qt l'appelle, et ne
remettent au widget que ce que le filtre laisse passer — la sémantique exacte
d'un filtre posé sur le `QApplication`. Le flux souris, lui, est éprouvé de bout
en bout avec le filtre réellement installé sur le `QApplication`.

Les délais sont pilotés par une horloge explicite : un test de veille de cinq
minutes ne dure pas cinq minutes.
