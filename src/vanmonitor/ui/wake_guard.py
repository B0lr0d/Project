"""Filtre d'événements : le premier geste réveille l'écran, et rien d'autre.

C'est la règle qui rend la veille acceptable dans un véhicule. Un écran noir ne
montre pas ce qui se trouve dessous : si le premier contact activait le bouton
placé sous le doigt, on ouvrirait un clapet ou on changerait un seuil sans
l'avoir voulu.

Le geste de réveil est donc **entièrement consommé** — l'appui, les
déplacements et le relâchement. Dès le doigt levé, l'interface redevient
normalement utilisable ; il n'y a pas de délai supplémentaire.

Toute interaction, réveil ou non, remet le compteur d'inactivité à zéro.

Trois flux, une seule règle
---------------------------
Une dalle tactile USB n'arrive pas toujours à Qt de la même façon, et le
programme ne choisit pas : cela dépend du greffon de plateforme, de la pile
d'entrée et de la configuration du système.

* **tactile natif** — ``TouchBegin`` / ``TouchUpdate`` / ``TouchEnd`` ;
* **souris synthétisée** — le tactile est exposé comme un pointeur, et seuls
  des ``MouseButtonPress`` / ``MouseMove`` / ``MouseButtonRelease`` arrivent ;
* **les deux à la fois** — Qt peut doubler un événement tactile non consommé
  par un événement souris synthétisé.

Le filtre ne cherche donc pas à deviner lequel de ces flux est utilisé : il
**suit chaque flux séparément** et n'arrête d'absorber que lorsque tous ceux
qu'il a vus s'ouvrir se sont refermés. Sans cela, dans le troisième cas, le
relâchement souris qui suit le ``TouchEnd`` atteindrait le widget.

Ce filtre se pose sur le ``QApplication``, jamais sur une fenêtre : Qt consulte
les filtres de l'application **avant** ceux de l'objet destinataire et avant
``event()``. Un événement absorbé ici n'atteint donc réellement personne.
"""

from __future__ import annotations

from PyQt5.QtCore import QEvent, QObject

from ..util.timebase import monotonic

#: Événements qui ouvrent un geste, et flux auquel ils appartiennent.
PRESS_EVENTS = {
    QEvent.MouseButtonPress: "souris",
    QEvent.MouseButtonDblClick: "souris",
    QEvent.TouchBegin: "tactile",
    QEvent.KeyPress: "clavier",
}

#: Événements qui referment un geste, et flux auquel ils appartiennent.
RELEASE_EVENTS = {
    QEvent.MouseButtonRelease: "souris",
    QEvent.TouchEnd: "tactile",
    QEvent.TouchCancel: "tactile",
    QEvent.KeyRelease: "clavier",
}

#: Interactions qui n'ouvrent ni ne referment de geste : un doigt qui glisse,
#: une molette. Elles réveillent et sont absorbées comme le reste.
MOVE_EVENTS = frozenset({
    QEvent.MouseMove,
    QEvent.TouchUpdate,
    QEvent.Wheel,
})

#: Tout ce qui compte comme une interaction de l'utilisateur.
ACTIVITY_EVENTS = frozenset(PRESS_EVENTS) | frozenset(RELEASE_EVENTS) | MOVE_EVENTS

#: Garde-fou : si le relâchement n'arrive jamais (geste interrompu, événement
#: perdu, doigt resté posé), on cesse d'avaler au bout de ce délai plutôt que de
#: bloquer l'interface.
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
        #: Flux dont on a vu l'ouverture sans encore voir la fermeture.
        self._open_streams: set[str] = set()
        self.swallowed_events = 0       # relevé, utile aux tests et au journal

    # ------------------------------------------------------------------
    @property
    def is_swallowing(self) -> bool:
        return self._swallowing_since is not None

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        event_type = event.type()
        if event_type not in ACTIVITY_EVENTS:
            return False

        now = monotonic()
        self._controller.note_activity(now)

        if self._controller.is_asleep:
            # Premier contact sur un écran noir : il ne sert qu'à réveiller.
            # Le réveil a lieu dans un autre thread et prend le temps qu'il
            # prend ; d'ici là les événements suivants du geste arrivent encore
            # avec l'écran marqué endormi, et doivent être absorbés eux aussi.
            if self._swallowing_since is None:
                self._swallowing_since = now
            self._worker.request_wake()

        if self._swallowing_since is None:
            return False

        self.swallowed_events += 1
        self._track_streams(event_type)

        expired = now - self._swallowing_since > MAX_SWALLOW_S
        if not self._open_streams or expired:
            # Le geste est terminé — ou n'a jamais annoncé sa fin. Dans les deux
            # cas l'interface redevient utilisable dès l'événement suivant.
            self._swallowing_since = None
            self._open_streams.clear()
        return True

    # ------------------------------------------------------------------
    def _track_streams(self, event_type: QEvent.Type) -> None:
        """Ouvre ou referme le flux auquel appartient cet événement.

        Un même geste peut emprunter deux flux en même temps (tactile natif
        doublé d'une souris synthétisée) : on n'a fini d'absorber que lorsque
        les deux se sont refermés.
        """
        stream = PRESS_EVENTS.get(event_type)
        if stream is not None:
            self._open_streams.add(stream)
            return
        stream = RELEASE_EVENTS.get(event_type)
        if stream is not None:
            self._open_streams.discard(stream)
