"""Analyse de la ligne de commande.

Le programme doit se lancer de la même façon sur un PC de développement et sur
le Raspberry : c'est une seule option, ``--sim``, qui change le matériel
utilisé — rien d'autre dans le code ne diffère.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

#: Emplacement normal sur le Raspberry : persistant, hors dépôt.
SYSTEM_CONFIG_PATH = Path("/var/lib/vanmonitor/config.json")

#: Repli sur un poste de développement, où /var/lib n'est pas accessible.
USER_CONFIG_PATH = Path.home() / ".config" / "vanmonitor" / "config.json"

ENV_CONFIG = "VANMONITOR_CONFIG"
ENV_SIM = "VANMONITOR_SIM"


@dataclass(frozen=True)
class Options:
    config_path: Path
    simulation: bool
    windowed: bool
    headless: bool
    duration_s: float | None
    log_level: str | None
    #: Taille de fenêtre demandée. Sur le fourgon, c'est la dalle qui décide ;
    #: sur un PC, cela permet d'éprouver les deux profils de disposition.
    screen_size: tuple[int, int] = (800, 480)
    no_sim_panel: bool = False


def default_config_path() -> Path:
    """Chemin de configuration par défaut, selon ce qui est accessible."""
    if os.environ.get(ENV_CONFIG):
        return Path(os.environ[ENV_CONFIG]).expanduser()

    if SYSTEM_CONFIG_PATH.exists():
        return SYSTEM_CONFIG_PATH
    try:
        SYSTEM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if os.access(SYSTEM_CONFIG_PATH.parent, os.W_OK):
            return SYSTEM_CONFIG_PATH
    except OSError:
        pass
    return USER_CONFIG_PATH


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vanmonitor",
        description="Monitoring et commande du fourgon aménagé.",
    )
    parser.add_argument(
        "--sim", action="store_true",
        help="matériel simulé : aucun capteur, aucun Raspberry nécessaire",
    )
    parser.add_argument(
        "--config", type=Path, default=None, metavar="FICHIER",
        help=f"fichier de configuration (défaut : {default_config_path()})",
    )
    parser.add_argument(
        "--windowed", action="store_true",
        help="fenêtré plutôt que plein écran (développement)",
    )
    parser.add_argument(
        "--headless", action="store_true",
        help="sans interface graphique : affiche l'état acquis dans le terminal",
    )
    parser.add_argument(
        "--duration", type=float, default=None, metavar="SECONDES",
        help="s'arrête automatiquement au bout de ce délai (mode sans interface)",
    )
    parser.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="niveau de journalisation (remplace la configuration)",
    )
    parser.add_argument(
        "--size", default=None, metavar="LxH",
        help="taille de fenêtre, ex. 800x480 ou 480x272 (développement)",
    )
    parser.add_argument(
        "--no-sim-panel", action="store_true",
        help="mode simulation sans le panneau de simulation",
    )
    return parser


def _parse_size(text: str | None) -> tuple[int, int]:
    if not text:
        return (800, 480)
    try:
        width, height = text.lower().split("x", 1)
        return max(320, int(width)), max(200, int(height))
    except (ValueError, AttributeError):
        return (800, 480)


def parse_args(argv: list[str] | None = None) -> Options:
    args = build_parser().parse_args(argv)
    simulation = args.sim or os.environ.get(ENV_SIM, "").lower() in {"1", "true", "yes"}
    return Options(
        config_path=(args.config.expanduser() if args.config else default_config_path()),
        simulation=simulation,
        windowed=args.windowed,
        headless=args.headless,
        duration_s=args.duration,
        log_level=args.log_level,
        screen_size=_parse_size(args.size),
        no_sim_panel=args.no_sim_panel,
    )
