"""Filtre d'événements : le premier toucher réveille l'écran, et rien d'autre.

C'est la règle qui rend la veille acceptable dans un véhicule. Un écran noir ne
montre pas ce qui se trouve dessous : si le premier contact activait le bouton
placé sous le doigt, on ouvrirait un clapet ou on changerait un seuil sans
l'avoir voulu.

Le geste de réveil est donc **entièrement consommé** — l'appui, les
déplacements et le relâchement. Dès le doigt levé, l'interface redevient
normalement utilisable ; il n'y a pas de délai supplémentaire.

Toute interaction, réveil ou non, remet le compteur d'inactivité à zéro.
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject

from ..util.timebase import monotonic

#: Événements qui comptent comme une interaction de l'utilisateur.
ACTIVITY_EVENTS = frozenset({
    QEvent.MouseButtonPress,
    QEvent.MouseButtonRelease,
    QEvent.MouseButtonDblClick,
    QEvent.MouseMove,
    QEvent.Wheel,
    QEvent.TouchBegin,
    QEvent.TouchUpdate,
    QEvent.TouchEnd,
    QEvent.KeyPress,
})

#: Événements qui terminent le geste de réveil.
RELEASE_EVENTS = frozenset({
    QEvent.MouseButtonRelease,
    QEvent.TouchEnd,
    QEvent.TouchCancel,
    QEvent.KeyRelease,
})

#: Garde-fou : si le relâchement n'arrive jamais (geste interrompu, événement
#: perdu), on cesse d'avaler au bout de ce délai plutôt que de bloquer l'écran.
MAX_SWALLOW_S = 2.0


class WakeGuard(QObject):
    """À installer sur le ``QApplication``.

    Ne connaît du matériel que le contrôleur de veille : aucune extinction n'a
    lieu dans le thread graphique, seulement une demande.
    """

    def __init__(self, controller, worker, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._controller = controller
        self._worker = worker
        self._swallowing_since: float | None = None
        self.swallowed_events = 0       # relevé, utile aux tests et au journal

    # ------------------------------------------------------------------
    @property
    def is_swallowing(self) -> bool:
        return self._swallowing_since is not None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type not in ACTIVITY_EVENTS and event_type not in RELEASE_EVENTS:
            return False

        now = monotonic()

        if self._controller.is_asleep:
            # Premier contact sur un écran noir : il ne sert qu'à réveiller.
            self._swallowing_since = now
            self.swallowed_events += 1
            self._controller.note_activity(now)
            self._worker.request_wake()
            return True

        if self._swallowing_since is not None:
            if (event_type in RELEASE_EVENTS
                    or now - self._swallowing_since > MAX_SWALLOW_S):
                self._swallowing_since = None
            self.swallowed_events += 1
            self._controller.note_activity(now)
            return True

        self._controller.note_activity(now)
        return False
