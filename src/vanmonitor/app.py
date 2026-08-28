"""Point d'assemblage — le seul module qui a le droit de tout connaître.

Il lit la configuration, construit le matériel (réel ou simulé), démarre les
threads d'acquisition et la boucle de contrôle, puis rend la main à
l'interface. Aucun autre module ne fait ce câblage.

En mode simulation, le panneau de simulation s'ouvre **à côté** de l'interface,
dans sa propre fenêtre : l'écran principal reste exactement celui du fourgon,
sans le moindre réglage de mise au point.
"""

from __future__ import annotations

import signal
import sys
import threading
from dataclasses import dataclass, field

from .cli import Options, parse_args
from .config import ConfigStore
from .core.acquisition import AcquisitionService
from .core.alerts import AlertEngine
from .core.commands import CommandBus
from .core.control_loop import ControlWorker
from .core.services import SnapshotBuilder
from .core.state import StateStore
from .hal.factory import HalBundle, build_hal
from .util.logging_setup import get_logger, setup_logging

logger = get_logger("app")


@dataclass
class Application:
    """L'installation complète, assemblée et prête à démarrer."""

    config: ConfigStore
    hal: HalBundle
    command_bus: CommandBus
    acquisition: AcquisitionService
    state: StateStore
    builder: SnapshotBuilder
    alerts: AlertEngine
    control: ControlWorker
    options: Options
    sensor_ids: list[str] = field(default_factory=list)
    #: Associations que la simulation établit d'office (zone → sonde simulée).
    implicit_bindings: dict = field(default_factory=dict)
    screen_size: tuple[int, int] = (800, 480)

    def start(self) -> None:
        self.acquisition.start()
        self.control.start()
        # Un premier tour immédiat : l'écran ne doit pas s'ouvrir vide.
        self.control.tick()

    def stop(self) -> None:
        self.control.request_stop()
        self.control.join(timeout=3.0)
        self.acquisition.stop()
        self.config.close()


def build_application(options: Options) -> Application:
    """Construit tout, sans rien démarrer."""
    config = ConfigStore(options.config_path)
    config.load()

    level = options.log_level or str(config.get("logging.level", "INFO"))
    setup_logging(level)

    simulation = options.simulation or bool(config.get("general.simulation", False))
    logger.info("configuration : %s", options.config_path)
    logger.info("mode : %s", "SIMULATION" if simulation else "matériel réel")

    hal = build_hal(config, simulation=simulation)
    command_bus = CommandBus()
    acquisition = AcquisitionService(hal, config, command_bus)
    state = StateStore()
    builder = SnapshotBuilder(config, simulation=simulation)
    alerts = AlertEngine(config)
    control = ControlWorker(acquisition, state, builder, alerts, period_s=1.0)

    return Application(
        config=config,
        hal=hal,
        command_bus=command_bus,
        acquisition=acquisition,
        state=state,
        builder=builder,
        alerts=alerts,
        control=control,
        options=options,
        sensor_ids=_available_sensor_ids(simulation),
        implicit_bindings=_implicit_bindings(config, simulation),
        screen_size=options.screen_size,
    )


def _implicit_bindings(config: ConfigStore, simulation: bool) -> dict:
    """Zones que la simulation relie automatiquement, sans rien écrire."""
    if not simulation:
        return {}
    from .constants import ZONE_ORDER
    from .hal.sim.sim_state import SIM_SENSOR_IDS
    return {
        zone: SIM_SENSOR_IDS[zone] for zone in ZONE_ORDER
        if not config.get(f"temperatures.zones.{zone.value}.sensor_id")
    }


def _available_sensor_ids(simulation: bool) -> list[str]:
    """Identifiants proposés dans la section Sondes des Paramètres."""
    if simulation:
        from .hal.sim.sim_state import SIM_SENSOR_IDS
        return list(SIM_SENSOR_IDS.values())
    from .hal.real.ds18b20 import scan_sensor_ids
    return scan_sensor_ids()


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------

def run(argv: list[str] | None = None) -> int:
    options = parse_args(argv)
    app = build_application(options)
    app.start()

    try:
        if options.headless:
            return _run_headless(app)
        return _run_gui(app)
    finally:
        app.stop()


def _run_headless(app: Application) -> int:
    """Boucle sans interface : affiche l'état acquis dans le terminal.

    Sert à vérifier la couche d'acquisition sans écran — et à s'assurer qu'elle
    ne dépend en rien de l'interface graphique.
    """
    # Rendu texte sans Qt : le mode sans interface ne dépend pas de PyQt5.
    from .ui.snapshot_text import format_snapshot

    stop = threading.Event()

    def _handle_signal(*_args: object) -> None:
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_signal)
        except ValueError:
            pass        # pas dans le thread principal : sans importance ici

    period_s = 2.0
    elapsed = 0.0
    while not stop.wait(period_s):
        elapsed += period_s
        print(format_snapshot(app.acquisition.snapshot()), flush=True)
        if app.options.duration_s is not None and elapsed >= app.options.duration_s:
            break
    return 0


def _run_gui(app: Application) -> int:
    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        print(
            "PyQt5 n'est pas installé.\n"
            "  Raspberry Pi OS : sudo apt install python3-pyqt5\n"
            "  PC              : pip install PyQt5\n"
            "Ou lancer sans interface : python -m vanmonitor --sim --headless",
            file=sys.stderr,
        )
        return 2

    from .ui.main_window import MainWindow

    qt_app = QApplication(sys.argv[:1])
    window = MainWindow(app)

    fullscreen = (bool(app.config.get("general.fullscreen", True))
                  and not app.options.windowed)
    if fullscreen:
        window.showFullScreen()
    else:
        window.show()

    # Le panneau de simulation est une fenêtre à part : l'écran du fourgon ne
    # contient aucun réglage de mise au point.
    panel = None
    if app.hal.simulation and not app.options.no_sim_panel:
        from .ui.sim_panel import SimulationPanel
        panel = SimulationPanel(app)
        panel.show()

    code = qt_app.exec_()
    if panel is not None:
        panel.close()
    return code


def main() -> None:     # pragma: no cover - point d'entrée
    raise SystemExit(run())
