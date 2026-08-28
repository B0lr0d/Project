"""Valeurs partagées entre les threads d'acquisition et le reste du programme.

``LatestValue`` est la pièce centrale de la garantie « une lecture lente ne
bloque personne » : un thread de matériel y **écrit** le fruit de sa lecture,
et tout le reste du programme y **lit** instantanément la dernière valeur
connue, sans jamais attendre le matériel.

Le verrou n'est tenu que le temps d'affecter quelques champs — jamais pendant
une entrée-sortie.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from ..constants import Status
from ..models import Sample
from ..util.logging_setup import get_logger
from ..util.timebase import monotonic

logger = get_logger("core.state")


class LatestValue:
    """Dernière valeur connue d'un équipement, avec son état de santé."""

    __slots__ = ("_name", "_lock", "_value", "_status", "_updated_at", "_reason")

    def __init__(self, name: str) -> None:
        self._name = name
        self._lock = threading.Lock()
        self._value: Any | None = None
        self._status = Status.ABSENT
        self._updated_at: float | None = None
        self._reason: str | None = "jamais lu"

    @property
    def name(self) -> str:
        return self._name

    def set(self, value: Any, measured_at: float | None = None) -> bool:
        """Publie une lecture réussie. Retourne False si elle est périmée.

        ``measured_at`` est l'instant où la **mesure a commencé**, pas celui où
        elle est publiée. La distinction compte : un thread déclaré bloqué puis
        remplacé peut se débloquer bien plus tard et vouloir publier ce qu'il a
        lu il y a une minute. Une mesure antérieure à celle déjà en place est
        donc refusée — sans quoi une valeur périmée écraserait une valeur
        fraîche.
        """
        stamp = monotonic() if measured_at is None else measured_at
        with self._lock:
            if self._updated_at is not None and stamp < self._updated_at:
                return False
            self._value = value
            self._status = Status.OK
            self._updated_at = stamp
            self._reason = None
            return True

    def mark_fault(self, reason: str, measured_at: float | None = None) -> bool:
        """Signale une erreur de lecture.

        La dernière valeur connue est conservée mais n'est plus valide. Comme
        pour ``set``, une erreur constatée avant la dernière mesure réussie est
        ignorée : un thread abandonné ne doit pas faire passer en panne un
        équipement qui répond de nouveau.
        """
        with self._lock:
            if (measured_at is not None and self._updated_at is not None
                    and measured_at < self._updated_at):
                return False
            self._status = Status.FAULT
            self._reason = reason
            return True

    def mark_absent(self, reason: str = "non configuré",
                    measured_at: float | None = None) -> bool:
        """Signale un équipement non associé ou non détecté.

        C'est un état **normal**, distinct d'une panne : à l'écran, ``--`` et
        non « Erreur capteur ». Une sonde débranchée n'est pas un capteur en
        défaut, c'est un capteur qui n'est plus là.
        """
        with self._lock:
            if (measured_at is not None and self._updated_at is not None
                    and measured_at < self._updated_at):
                return False
            self._value = None
            self._status = Status.ABSENT
            self._reason = reason
            return True

    def get(self, now: float | None = None, stale_after_s: float | None = None) -> Sample:
        """Lecture immédiate, jamais bloquante.

        Une valeur trop ancienne bascule en ``STALE`` : c'est ce qui distingue
        « le capteur répond » de « le capteur a répondu il y a dix minutes ».
        """
        instant = monotonic() if now is None else now
        with self._lock:
            value, status, updated_at, reason = (
                self._value, self._status, self._updated_at, self._reason,
            )

        age = None if updated_at is None else max(0.0, instant - updated_at)
        if (status is Status.OK and stale_after_s is not None
                and age is not None and age > stale_after_s):
            status = Status.STALE
            reason = f"aucune lecture depuis {age:.0f} s"

        return Sample(value=value, status=status, updated_at=updated_at,
                      age_s=age, reason=reason)


class StateStore:
    """Dernier objet publié vers l'interface, et ses abonnés.

    L'objet publié est immuable : l'interface le lit sans verrou et sans
    risque de le voir changer sous ses pieds pendant qu'elle dessine.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._current: Any | None = None
        self._listeners: list[Callable[[Any], None]] = []

    def publish(self, snapshot: Any) -> None:
        with self._lock:
            self._current = snapshot
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(snapshot)
            except Exception:       # un abonné fautif ne casse pas la boucle
                logger.exception("abonné à l'état en erreur")

    def get(self) -> Any | None:
        with self._lock:
            return self._current

    def add_listener(self, listener: Callable[[Any], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[Any], None]) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)
