"""La règle d'architecture la plus importante, vérifiée automatiquement.

``core/`` et ``ui/`` ne doivent connaître du matériel que ``hal.interfaces``.
S'ils importaient un pilote réel ou un mock, changer de matériel ne serait plus
une affaire de configuration — et le mode simulation cesserait d'être fidèle.

Une seule exception, explicite : ``ui/sim_panel.py`` pilote le fourgon simulé,
c'est sa raison d'être.
"""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src" / "vanmonitor"

#: Modules autorisés à toucher au matériel malgré leur emplacement.
EXEMPTIONS = {
    "ui/sim_panel.py",      # le panneau de simulation manipule le monde simulé
}

FORBIDDEN_PREFIXES = ("hal.real", "hal.sim", "vanmonitor.hal.real", "vanmonitor.hal.sim")


def _imported_modules(path: Path) -> set[str]:
    """Modules importés par un fichier, chemins relatifs résolus."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = path.relative_to(SOURCE_ROOT).parts[:-1]
    modules: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                modules.add(node.module or "")
                continue
            # Import relatif : on le résout en chemin pointé depuis vanmonitor.
            base = list(package_parts)
            for _ in range(node.level - 1):
                if base:
                    base.pop()
            if node.module:
                base.extend(node.module.split("."))
            modules.add(".".join(base))
    return modules


def _python_files(subdirectory: str) -> list[Path]:
    return sorted((SOURCE_ROOT / subdirectory).rglob("*.py"))


def test_core_and_ui_never_import_hardware() -> None:
    offenders: list[str] = []

    for subdirectory in ("core", "ui"):
        for path in _python_files(subdirectory):
            relative = path.relative_to(SOURCE_ROOT).as_posix()
            if relative in EXEMPTIONS:
                continue
            for module in _imported_modules(path):
                if module.startswith(FORBIDDEN_PREFIXES):
                    offenders.append(f"{relative} importe {module}")

    assert not offenders, (
        "core/ et ui/ ne doivent connaître que hal.interfaces :\n  "
        + "\n  ".join(offenders)
    )


def test_hal_interfaces_stays_hardware_free() -> None:
    """Le contrat matériel ne doit dépendre d'aucune bibliothèque matérielle."""
    modules = _imported_modules(SOURCE_ROOT / "hal" / "interfaces.py")
    for suspicious in ("serial", "gpiozero", "lgpio", "RPi", "PyQt5"):
        assert not any(module.split(".")[0] == suspicious for module in modules), (
            f"hal/interfaces.py ne doit pas importer {suspicious}"
        )


def test_core_does_not_import_qt() -> None:
    """La logique métier doit être testable sans interface graphique."""
    offenders = [
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in _python_files("core")
        if any(module.split(".")[0] == "PyQt5" for module in _imported_modules(path))
    ]
    assert not offenders, f"core/ ne doit pas dépendre de Qt : {offenders}"


def test_hal_real_defers_hardware_imports() -> None:
    """Les bibliothèques matérielles ne s'importent pas au chargement du module.

    Sans cela, l'absence de ``pyserial`` sur un PC de développement empêcherait
    le programme de démarrer, y compris en simulation.
    """
    for path in _python_files("hal/real"):
        modules = _imported_modules(path)
        for suspicious in ("serial", "gpiozero", "lgpio", "RPi"):
            assert not any(module.split(".")[0] == suspicious for module in modules), (
                f"{path.name} importe {suspicious} au niveau du module ; "
                "l'import doit être différé dans le constructeur"
            )
