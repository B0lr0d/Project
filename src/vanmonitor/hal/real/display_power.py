"""Extinction et rallumage de l'affichage sur Raspberry Pi OS.

Écran retenu : **Waveshare 5 pouces HDMI LCD (H) V4** — 800 × 480, tactile
capacitif, image par HDMI, tactile par USB, orientation paysage.

Le fait que les deux liaisons soient distinctes est ce qui rend la veille
utilisable : couper la sortie HDMI n'éteint pas le contrôleur tactile USB, donc
le doigt réveille l'écran. Aucune configuration particulière n'est nécessaire
pour cela.

Reste que la manière d'éteindre une sortie HDMI dépend de la pile graphique en
place (X11 ou KMS, avec ou sans serveur d'affichage), et pas seulement de la
dalle. Trois méthodes sont donc essayées dans l'ordre, et le choix peut être
forcé par la configuration si le comportement réel du Waveshare demande à être
ajusté :

``vcgencmd``
    ``vcgencmd display_power 0|1`` — coupe la sortie du Raspberry lui-même.
    Fonctionne sans serveur graphique et c'est la méthode la plus directe.

``xset``
    ``xset dpms force off|on`` — passe par la gestion d'énergie de X11.
    Demande un serveur X et une variable ``DISPLAY``.

``backlight``
    ``/sys/class/backlight/*/bl_power`` — pilotage du rétroéclairage. Prévu pour
    les dalles qui l'exposent ; le Waveshare en HDMI ne le fait probablement
    pas, mais la méthode ne coûte rien à garder.

Si aucune ne convient, ``NullDisplayPower`` prend le relais : la veille est
alors annoncée comme indisponible plutôt que silencieusement inopérante.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ...constants import DisplayState
from ...util.logging_setup import get_logger
from ..interfaces import DisplayPower, HardwareError

logger = get_logger("hal.display")

#: Une commande d'extinction doit répondre vite : elle est déclenchée par un
#: doigt sur l'écran, et l'attente se verrait.
COMMAND_TIMEOUT_S = 3.0

BACKLIGHT_ROOT = Path("/sys/class/backlight")


class NullDisplayPower(DisplayPower):
    """Aucune méthode d'extinction disponible : la veille reste inopérante.

    Ce n'est pas une panne — un PC de développement n'a rien à éteindre. Mais
    la veille doit le dire, plutôt que de laisser croire qu'elle fonctionne.
    """

    def __init__(self, reason: str = "aucune méthode disponible") -> None:
        self._reason = reason

    def sleep(self) -> None:
        raise HardwareError(f"veille d'écran indisponible : {self._reason}")

    def wake(self) -> None:
        return None                 # rallumer ce qui n'a jamais été éteint

    def state(self) -> DisplayState:
        return DisplayState.INCONNU

    def is_available(self) -> bool:
        return False

    def describe(self) -> str:
        return f"indisponible ({self._reason})"


class CommandDisplayPower(DisplayPower):
    """Extinction par une commande externe (``vcgencmd`` ou ``xset``)."""

    def __init__(self, name: str, off: list[str], on: list[str],
                 *, timeout_s: float = COMMAND_TIMEOUT_S) -> None:
        self._name = name
        self._off = off
        self._on = on
        self._timeout_s = timeout_s
        self._commanded = DisplayState.ON

    def sleep(self) -> None:
        self._run(self._off)
        self._commanded = DisplayState.OFF

    def wake(self) -> None:
        self._run(self._on)
        self._commanded = DisplayState.ON

    def state(self) -> DisplayState:
        # Ces commandes ne relisent pas la dalle : on connaît l'ordre donné,
        # pas ce que l'écran en a fait. Même distinction que pour les clapets.
        return self._commanded

    def is_available(self) -> bool:
        return True

    def describe(self) -> str:
        return self._name

    def _run(self, argv: list[str]) -> None:
        try:
            completed = subprocess.run(
                argv, capture_output=True, timeout=self._timeout_s, check=False,
            )
        except OSError as exc:
            raise HardwareError(f"{self._name} : {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise HardwareError(
                f"{self._name} : pas de réponse en {self._timeout_s:g} s"
            ) from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()
            raise HardwareError(f"{self._name} : code {completed.returncode} {detail}")


class BacklightDisplayPower(DisplayPower):
    """Extinction par ``/sys/class/backlight/<dalle>/bl_power``."""

    def __init__(self, node: Path) -> None:
        self._node = node
        self._file = node / "bl_power"

    def sleep(self) -> None:
        self._write(1)              # 1 = FB_BLANK_POWERDOWN

    def wake(self) -> None:
        self._write(0)

    def state(self) -> DisplayState:
        try:
            value = int(self._file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return DisplayState.INCONNU
        return DisplayState.ON if value == 0 else DisplayState.OFF

    def is_available(self) -> bool:
        return self._file.exists()

    def describe(self) -> str:
        return f"backlight ({self._node.name})"

    def _write(self, value: int) -> None:
        try:
            self._file.write_text(f"{value}\n", encoding="ascii")
        except OSError as exc:
            raise HardwareError(f"rétroéclairage {self._node.name} : {exc}") from exc


# ---------------------------------------------------------------------------
# Choix de la méthode
# ---------------------------------------------------------------------------

def _vcgencmd() -> DisplayPower | None:
    binary = shutil.which("vcgencmd")
    if binary is None:
        return None
    return CommandDisplayPower(
        "vcgencmd", [binary, "display_power", "0"], [binary, "display_power", "1"],
    )


def _xset() -> DisplayPower | None:
    import os

    binary = shutil.which("xset")
    if binary is None or not os.environ.get("DISPLAY"):
        return None
    return CommandDisplayPower(
        "xset dpms",
        [binary, "dpms", "force", "off"],
        [binary, "dpms", "force", "on"],
    )


def _backlight() -> DisplayPower | None:
    try:
        nodes = sorted(BACKLIGHT_ROOT.iterdir())
    except OSError:
        return None
    for node in nodes:
        driver = BacklightDisplayPower(node)
        if driver.is_available():
            return driver
    return None


#: Ordre d'essai en mode automatique.
METHODS = {
    "vcgencmd": _vcgencmd,
    "xset": _xset,
    "backlight": _backlight,
}


def build_display_power(method: str = "auto") -> DisplayPower:
    """Construit la méthode d'extinction, sans jamais lever.

    ``method`` vaut ``auto`` (essaie les méthodes dans l'ordre), le nom d'une
    méthode précise, ou ``none`` pour désactiver toute extinction.
    """
    if method == "none":
        return NullDisplayPower("désactivée par la configuration")

    if method != "auto":
        builder = METHODS.get(method)
        if builder is None:
            return NullDisplayPower(f"méthode inconnue : {method}")
        try:
            driver = builder()
        except Exception as exc:        # une méthode fautive n'en bloque pas d'autres
            return NullDisplayPower(f"{method} : {exc}")
        if driver is None:
            return NullDisplayPower(f"{method} non disponible sur ce système")
        logger.info("veille d'écran : méthode %s (imposée)", driver.describe())
        return driver

    for name, builder in METHODS.items():
        try:
            driver = builder()
        except Exception as exc:
            logger.debug("veille d'écran : %s écartée (%s)", name, exc)
            continue
        if driver is not None:
            logger.info("veille d'écran : méthode %s", driver.describe())
            return driver

    logger.info("veille d'écran : aucune méthode disponible sur ce système")
    return NullDisplayPower()
