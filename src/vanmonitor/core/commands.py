"""File de commandes : le seul chemin de l'interface vers le matériel.

L'interface graphique n'appelle jamais un pilote. Elle dépose une commande ici,
et un thread dédié l'exécute. Deux conséquences voulues :

* un appui sur ``OUVRIR`` ne peut pas figer l'écran, même si l'actionneur met
  dix secondes à répondre ou ne répond pas du tout ;
* l'écran n'affiche jamais un état supposé — il attend de relire le clapet.

La même file servira à l'étape 7 pour les décisions automatiques du chauffage :
la logique décide, le thread des clapets exécute.
"""

from __future__ import annotations

import queue
from dataclasses import dataclass, field

from ..constants import CircuitId, ValveCommand
from ..util.timebase import monotonic


@dataclass(frozen=True)
class Command:
    """Commande générique. Toutes portent l'instant de leur dépôt."""

    submitted_at: float = field(default_factory=monotonic)


@dataclass(frozen=True)
class ManualValveCommand(Command):
    """Ordre direct sur un clapet, venu de l'écran ou de la logique chauffage."""

    circuit: CircuitId = CircuitId.LOCAL_EAU
    action: ValveCommand = ValveCommand.STOP

    def __post_init__(self) -> None:
        if self.action is ValveCommand.NONE:
            raise ValueError("ValveCommand.NONE n'est pas un ordre exécutable")


class CommandBus:
    """File d'attente bornée, sans blocage côté déposant.

    La borne est volontaire : si le matériel ne répond plus, mieux vaut
    refuser les ordres les plus anciens que gonfler indéfiniment une file que
    personne ne consomme.
    """

    def __init__(self, max_size: int = 64) -> None:
        self._queue: queue.Queue[Command] = queue.Queue(maxsize=max_size)
        self._dropped = 0

    def submit(self, command: Command) -> bool:
        """Dépose une commande. Retourne False si la file est saturée."""
        try:
            self._queue.put_nowait(command)
            return True
        except queue.Full:
            self._dropped += 1
            return False

    def get(self, timeout: float) -> Command | None:
        """Attend une commande au plus ``timeout`` secondes."""
        try:
            return self._queue.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def drain(self) -> list[Command]:
        """Retire et rend toutes les commandes en attente, sans bloquer."""
        commands: list[Command] = []
        while True:
            try:
                commands.append(self._queue.get_nowait())
            except queue.Empty:
                return commands

    @property
    def dropped(self) -> int:
        """Nombre de commandes refusées depuis le démarrage (file saturée)."""
        return self._dropped

    def __len__(self) -> int:
        return self._queue.qsize()
