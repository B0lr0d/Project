"""Magasin de configuration : chargement, accès par chemin, écriture atomique.

Contraintes traitées ici :

* **Coupure d'alimentation brutale** — l'écriture passe par un fichier
  temporaire suivi d'un ``os.replace`` atomique, précédé d'une copie de secours
  ``config.bak``. Un fichier tronqué ne peut donc jamais faire perdre les
  réglages.
* **Usure de la carte microSD** — les écritures sont différées de deux secondes
  et regroupées : déplacer un curseur ne déclenche qu'une seule écriture.
* **Testabilité** — ce module ne dépend pas de Qt (voir la note d'écart dans
  ``docs/ARCHITECTURE.md``) : la notification passe par de simples fonctions de
  rappel, ce qui permet de tester la configuration sans interface graphique.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Callable

from ..util.logging_setup import get_logger
from .defaults import default_config
from .schema import validate

logger = get_logger("config")

Listener = Callable[[str], None]

#: Délai de regroupement des écritures disque.
SAVE_DEBOUNCE_S = 2.0


class ConfigStore:
    """Configuration en mémoire, persistée sur disque de façon atomique."""

    def __init__(self, path: Path | str, *, debounce_s: float = SAVE_DEBOUNCE_S) -> None:
        self._path = Path(path)
        self._backup_path = self._path.with_suffix(self._path.suffix + ".bak")
        self._debounce_s = max(0.0, debounce_s)

        self._lock = threading.RLock()
        self._data: dict[str, Any] = default_config()
        self._listeners: list[Listener] = []
        self._timer: threading.Timer | None = None
        self._dirty = False
        self._warnings: list[str] = []

    # ------------------------------------------------------------------
    # Chargement
    # ------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    @property
    def warnings(self) -> list[str]:
        """Anomalies rencontrées au dernier chargement."""
        with self._lock:
            return list(self._warnings)

    def load(self) -> list[str]:
        """Charge le fichier, ou la sauvegarde, ou les valeurs par défaut.

        Ne lève jamais : une configuration illisible ne doit pas empêcher le
        démarrage. Retourne la liste des avertissements.
        """
        raw, warnings = self._read_first_readable()
        data, validation_warnings = validate(raw)
        warnings.extend(validation_warnings)

        with self._lock:
            self._data = data
            self._warnings = warnings

        for message in warnings:
            logger.warning("configuration : %s", message)
        return warnings

    def _read_first_readable(self) -> tuple[Any, list[str]]:
        warnings: list[str] = []
        for candidate, label in ((self._path, "fichier"), (self._backup_path, "sauvegarde")):
            if not candidate.exists():
                continue
            try:
                with candidate.open("r", encoding="utf-8") as handle:
                    return json.load(handle), warnings
            except (OSError, ValueError) as exc:
                warnings.append(f"{label} {candidate.name} illisible ({exc})")
        if not warnings and not self._path.exists():
            logger.info("aucune configuration existante — valeurs par défaut")
        return None, warnings

    # ------------------------------------------------------------------
    # Accès
    # ------------------------------------------------------------------
    def get(self, path: str, default: Any = None) -> Any:
        """Lit une valeur par chemin pointé, ex. ``"heating.circuits.cabine.mode"``."""
        with self._lock:
            node: Any = self._data
            for part in path.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return _copy(node)

    def section(self, path: str) -> dict[str, Any]:
        """Copie d'une section entière ; dictionnaire vide si absente."""
        value = self.get(path, {})
        return value if isinstance(value, dict) else {}

    def as_dict(self) -> dict[str, Any]:
        with self._lock:
            return _copy(self._data)

    def set(self, path: str, value: Any, *, notify: bool = True) -> bool:
        """Écrit une valeur et planifie la sauvegarde. Retourne True si modifiée."""
        parts = path.split(".")
        with self._lock:
            node: Any = self._data
            for part in parts[:-1]:
                if not isinstance(node, dict) or part not in node:
                    raise KeyError(f"chemin de configuration inconnu : {path}")
                node = node[part]
            leaf = parts[-1]
            if not isinstance(node, dict) or leaf not in node:
                raise KeyError(f"chemin de configuration inconnu : {path}")
            if node[leaf] == value:
                return False
            node[leaf] = _copy(value)
            self._dirty = True
            self._schedule_save_locked()

        if notify:
            self._notify(path)
        return True

    def update(self, values: dict[str, Any]) -> list[str]:
        """Applique plusieurs chemins d'un coup. Retourne ceux qui ont changé."""
        changed = [path for path, value in values.items()
                   if self.set(path, value, notify=False)]
        for path in changed:
            self._notify(path)
        return changed

    def reset_section(self, path: str) -> None:
        """Rétablit une section entière à sa valeur par défaut."""
        defaults: Any = default_config()
        for part in path.split("."):
            if not isinstance(defaults, dict) or part not in defaults:
                raise KeyError(f"section de configuration inconnue : {path}")
            defaults = defaults[part]

        parts = path.split(".")
        with self._lock:
            node: Any = self._data
            for part in parts[:-1]:
                node = node[part]
            node[parts[-1]] = _copy(defaults)
            self._dirty = True
            self._schedule_save_locked()
        self._notify(path)

    # ------------------------------------------------------------------
    # Notification
    # ------------------------------------------------------------------
    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _notify(self, path: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(path)
            except Exception:       # un abonné fautif ne casse pas les autres
                logger.exception("abonné à la configuration en erreur (%s)", path)

    # ------------------------------------------------------------------
    # Écriture
    # ------------------------------------------------------------------
    def _schedule_save_locked(self) -> None:
        if self._debounce_s <= 0:
            self._save()
            return
        if self._timer is not None:
            self._timer.cancel()
        self._timer = threading.Timer(self._debounce_s, self._save)
        self._timer.daemon = True
        self._timer.start()

    def save_now(self) -> bool:
        """Écrit immédiatement si nécessaire. Retourne True si un fichier a été écrit."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        return self._save()

    def _save(self) -> bool:
        with self._lock:
            if not self._dirty:
                return False
            payload = json.dumps(self._data, indent=2, ensure_ascii=False, sort_keys=False)
            self._timer = None

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            if self._path.exists():
                shutil.copy2(self._path, self._backup_path)

            temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
            with temp_path.open("w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self._path)

            # Le renommage lui-même n'est durable qu'après synchronisation du
            # répertoire : sans cela, une coupure peut laisser l'ancien nom.
            try:
                directory = os.open(self._path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError:
                pass    # tous les systèmes de fichiers ne le permettent pas
        except OSError as exc:
            logger.error("écriture de la configuration impossible (%s) : %s",
                         self._path, exc)
            return False

        with self._lock:
            self._dirty = False
        logger.debug("configuration écrite dans %s", self._path)
        return True

    def close(self) -> None:
        """Annule la temporisation et écrit ce qui reste en attente."""
        self.save_now()


def _copy(value: Any) -> Any:
    """Copie défensive des structures mutables."""
    if isinstance(value, dict):
        return {key: _copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_copy(item) for item in value]
    return value
