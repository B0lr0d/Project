"""Clapet de chauffage simulé.

Ce mock existe en **deux variantes**, et c'est volontaire : l'actionneur réel
n'est pas choisi, et on ne sait pas encore s'il fournira une position réelle.

* ``feedback = True``  — le matériel confirme sa position.
* ``feedback = False`` — le matériel n'en dit rien : ``get_confirmed_state()``
  renvoie toujours ``INCONNU``, et l'interface devra écrire « commandé » au
  lieu d'un état sec.

C'est le cas sans retour qui doit être éprouvé le plus tôt possible, car c'est
lui qui contraint l'affichage.
"""

from __future__ import annotations

from ...constants import CircuitId, ConfirmedState, ValveCommand, ValveState
from ...util.timebase import monotonic
from ..interfaces import ValveDriver, ValveError
from .sim_state import SimState


class MockValveDriver(ValveDriver):
    """Clapet motorisé simulé, avec temps de course."""

    def __init__(
        self,
        circuit: CircuitId,
        sim_state: SimState,
        *,
        timeout_s: float = 5.0,
    ) -> None:
        self._circuit = circuit
        self._sim = sim_state
        self._timeout_s = timeout_s
        self._commanded_since: float | None = None

    # ------------------------------------------------------------------
    # Commandes
    # ------------------------------------------------------------------
    def open(self) -> None:
        self._command(ValveCommand.OPEN)

    def close(self) -> None:
        self._command(ValveCommand.CLOSE)

    def stop(self) -> None:
        self._command(ValveCommand.STOP)

    def _command(self, command: ValveCommand) -> None:
        if self._sim.valve(self._circuit).fault:
            raise ValveError(f"clapet {self._circuit.value} : actionneur en défaut")
        self._sim.command_valve(self._circuit, command)
        self._commanded_since = monotonic()

    # ------------------------------------------------------------------
    # Lecture d'état
    # ------------------------------------------------------------------
    def get_commanded_state(self) -> ValveCommand:
        return self._sim.valve(self._circuit).commanded

    def has_position_feedback(self) -> bool:
        return self._sim.valve(self._circuit).feedback

    def get_confirmed_state(self) -> ConfirmedState:
        valve = self._sim.valve(self._circuit)
        if not valve.feedback:
            # Interdit de déduire quoi que ce soit de la commande.
            return ConfirmedState.INCONNU
        if valve.position >= 1.0:
            return ConfirmedState.OUVERT
        if valve.position <= 0.0:
            return ConfirmedState.FERME
        return ConfirmedState.INCONNU       # en cours de course

    def get_state(self) -> ValveState:
        valve = self._sim.valve(self._circuit)
        if valve.fault:
            return ValveState.ERREUR

        if valve.feedback:
            if valve.moving:
                return (ValveState.OUVERTURE if valve.target > valve.position
                        else ValveState.FERMETURE)
            return ValveState.OUVERT if valve.position >= 1.0 else ValveState.FERME

        # Sans retour de position : on ne dispose que de l'ordre et du temps
        # écoulé. La valeur rendue ici n'est jamais présentée comme certaine.
        if valve.commanded is ValveCommand.NONE:
            return ValveState.INCONNU
        if valve.moving:
            return (ValveState.OUVERTURE if valve.target > valve.position
                    else ValveState.FERMETURE)
        if valve.commanded is ValveCommand.OPEN:
            return ValveState.OUVERT
        if valve.commanded is ValveCommand.CLOSE:
            return ValveState.FERME
        return ValveState.INCONNU           # arrêt en cours de course

    def has_fault(self) -> bool:
        return self._sim.valve(self._circuit).fault

    # ------------------------------------------------------------------
    def position(self) -> float:
        """Position physique réelle (0 fermé, 1 ouvert).

        N'existe **que** dans le simulateur : sert au panneau de simulation à
        montrer la réalité, y compris quand le logiciel, lui, l'ignore.
        """
        return self._sim.valve(self._circuit).position

    def __repr__(self) -> str:      # pragma: no cover - confort de débogage
        feedback = "avec retour" if self.has_position_feedback() else "sans retour"
        return f"<MockValveDriver {self._circuit.value} {feedback}>"
