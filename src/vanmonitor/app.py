"""Point d'assemblage — le seul module qui a le droit de tout connaître.

Il lit la configuration, construit le matériel (réel ou simulé), démarre les
threads d'acquisition et la boucle de contrôle, puis rend la main à
l'interface. Aucun autre module ne fait ce câblage.

À l'étape 2, l'interface disponible est le **panneau de simulation** : il pilote
le fourgon virtuel et montre, en regard, ce que la couche d'acquisition en
perçoit réellement. L'écran Accueil et l'écran Paramètres arrivent à l'étape 3.
"""

from __future__ import annotations

import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .cli import Options, parse_args
from .config import ConfigStore
from .core.acquisition import AcquisitionService
from .core.commands import CommandBus
from .core.control_loop import ControlWorker
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
    control: ControlWorker
    options: Options

    def start(self) -> None:
        self.acquisition.start()
        self.control.start()

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
    control = ControlWorker(
        acquisition, state,
        period_s=lambda: 1.0,
    )

    return Application(
        config=config,
        hal=hal,
        command_bus=command_bus,
        acquisition=acquisition,
        state=state,
        control=control,
        options=options,
    )


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

    from .ui.sim_panel import SimulationPanel

    if not app.hal.simulation:
        print(
            "L'interface graphique de l'étape 2 est le panneau de simulation.\n"
            "Relancer avec --sim, ou attendre l'étape 3 pour l'écran Accueil.",
            file=sys.stderr,
        )
        return 2

    qt_app = QApplication(sys.argv[:1])
    panel = SimulationPanel(app)
    if app.options.windowed:
        panel.show()
    else:
        panel.show()        # l'étape 12 mettra l'écran Accueil en plein écran
    return qt_app.exec_()


def main() -> None:     # pragma: no cover - point d'entrée
    raise SystemExit(run())
