"""Threads d'acquisition, chien de garde et superviseur.

C'est ici que sont tenues les trois garanties de l'architecture :

1. **Délai d'expiration sur toute I/O** — les pilotes reçoivent leur délai à la
   construction et s'engagent à rendre la main ou à lever ``HardwareTimeout``.
2. **Aucune lecture lente ne bloque une autre acquisition** — chaque famille de
   matériel tourne dans son propre thread et publie dans son propre
   ``LatestValue``. Le reste du programme lit de la mémoire, jamais du
   matériel.
3. **Aucune I/O matérielle dans le thread graphique** — l'interface n'a aucune
   référence vers le HAL ; elle lit un instantané et dépose des commandes.

Et la limite assumée : un thread Python bloqué dans un appel système ne peut
pas être tué. Le chien de garde ne prétend donc pas l'interrompre — il le
déclare bloqué, l'abandonne (thread *daemon*) et démarre un remplaçant, avec
une temporisation croissante pour ne pas créer de threads en rafale.
"""

from __future__ import annotations

import threading
from typing import Callable

from ..constants import (
    CIRCUIT_ORDER,
    CircuitId,
    ConfirmedState,
    Status,
    TANK_ORDER,
    TankId,
    ValveCommand,
    ValveState,
    ZONE_ORDER,
    ZoneId,
)
from ..models import AcquisitionSnapshot, Sample, ValveObservation, WorkerHealth
from ..util.logging_setup import get_logger
from ..util.ratelimit import RateLimitedLogger
from ..util.timebase import monotonic
from .commands import Command, CommandBus, ManualValveCommand
from .state import LatestValue

logger = get_logger("core.workers")
limited = RateLimitedLogger(logger)

PeriodProvider = Callable[[], float]


def _as_provider(value: float | PeriodProvider) -> PeriodProvider:
    if callable(value):
        return value
    return lambda: float(value)


# ---------------------------------------------------------------------------
# Thread générique d'acquisition
# ---------------------------------------------------------------------------

class HardwareWorker(threading.Thread):
    """Exécute périodiquement une tâche d'acquisition, sans jamais mourir.

    La tâche (``task_fn``) est responsable de publier ce qu'elle a lu dans les
    ``LatestValue`` concernés ; le thread, lui, apporte la périodicité, le
    confinement des exceptions et la mesure de santé.

    Une exception, quelle qu'elle soit, est journalisée (avec limitation de
    débit) et n'interrompt pas la boucle : une panne matérielle ne doit jamais
    faire disparaître une famille d'acquisition.
    """

    def __init__(
        self,
        name: str,
        task_fn: Callable[[], None],
        *,
        period_s: float | PeriodProvider,
        deadline_s: float,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self._task_fn = task_fn
        self._period = _as_provider(period_s)
        self._deadline_s = max(0.1, deadline_s)
        self._on_error = on_error

        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._active = True
        self._call_started_at: float | None = None
        self._last_success: float | None = None
        self._consecutive_failures = 0
        self._restarts = 0
        self._last_error: str | None = None

    # ------------------------------------------------------------------
    @property
    def deadline_s(self) -> float:
        return self._deadline_s

    def request_stop(self) -> None:
        """Demande l'arrêt. Un thread bloqué s'arrêtera à son déblocage."""
        self._stop_event.set()

    def retire(self) -> None:
        """Retire le thread du service : il ne publiera plus rien.

        Appelé avant de démarrer un remplaçant, pour qu'un thread débloqué
        tardivement n'écrase pas une valeur plus récente.
        """
        with self._lock:
            self._active = False
        self._stop_event.set()

    def note_restart(self, count: int) -> None:
        with self._lock:
            self._restarts = count

    # ------------------------------------------------------------------
    def run(self) -> None:       # pragma: no cover - exercé via les tests d'intégration
        while not self._stop_event.is_set():
            started = monotonic()
            with self._lock:
                self._call_started_at = started

            error: str | None = None
            try:
                self._task_fn()
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"

            with self._lock:
                self._call_started_at = None
                if not self._active:
                    return          # remplacé : ne rien publier de plus
                if error is None:
                    self._last_success = monotonic()
                    self._consecutive_failures = 0
                    self._last_error = None
                else:
                    self._consecutive_failures += 1
                    self._last_error = error

            if error is not None:
                limited.error(f"worker.{self.name}", f"{self.name} : {error}")
                if self._on_error is not None:
                    try:
                        self._on_error(error)
                    except Exception:
                        logger.exception("gestionnaire d'erreur du worker %s", self.name)

            elapsed = monotonic() - started
            self._stop_event.wait(max(0.0, self._period() - elapsed))

    # ------------------------------------------------------------------
    def health(self, now: float | None = None) -> WorkerHealth:
        instant = monotonic() if now is None else now
        with self._lock:
            started = self._call_started_at
            stuck = started is not None and (instant - started) > self._deadline_s
            return WorkerHealth(
                name=self.name,
                last_success=self._last_success,
                consecutive_failures=self._consecutive_failures,
                stuck=stuck,
                restarts=self._restarts,
                running=self.is_alive() and self._active,
                last_error=self._last_error,
            )


# ---------------------------------------------------------------------------
# Superviseur
# ---------------------------------------------------------------------------

class WorkerSupervisor:
    """Surveille les threads d'acquisition et remplace ceux qui sont bloqués."""

    def __init__(self, backoff_s: list[float] | None = None) -> None:
        self._backoff = [float(item) for item in (backoff_s or [5, 15, 60, 300])]
        self._lock = threading.RLock()
        self._factories: dict[str, Callable[[], HardwareWorker]] = {}
        self._workers: dict[str, HardwareWorker] = {}
        self._restarts: dict[str, int] = {}
        self._next_restart_at: dict[str, float] = {}
        self._order: list[str] = []

    # ------------------------------------------------------------------
    def register(self, factory: Callable[[], HardwareWorker]) -> HardwareWorker:
        """Crée un thread à partir de sa fabrique et le prend en charge."""
        worker = factory()
        with self._lock:
            self._factories[worker.name] = factory
            self._workers[worker.name] = worker
            self._restarts.setdefault(worker.name, 0)
            if worker.name not in self._order:
                self._order.append(worker.name)
        return worker

    def start_all(self) -> None:
        with self._lock:
            workers = [self._workers[name] for name in self._order]
        for worker in workers:
            if not worker.is_alive():
                worker.start()

    def stop_all(self, timeout_s: float = 3.0) -> None:
        with self._lock:
            workers = list(self._workers.values())
        for worker in workers:
            worker.request_stop()
        deadline = monotonic() + max(0.0, timeout_s)
        for worker in workers:
            remaining = max(0.0, deadline - monotonic())
            worker.join(timeout=remaining)
            if worker.is_alive():
                # Thread bloqué dans un appel système : il est *daemon*, il
                # n'empêchera pas le programme de se terminer.
                logger.warning("thread %s toujours bloqué à l'arrêt — abandonné",
                               worker.name)

    # ------------------------------------------------------------------
    def check(self, now: float | None = None) -> list[WorkerHealth]:
        """Relève la santé de tous les threads et remplace ceux qui sont bloqués.

        Appelée à chaque tour de la boucle de contrôle.
        """
        instant = monotonic() if now is None else now
        report: list[WorkerHealth] = []

        with self._lock:
            names = list(self._order)

        for name in names:
            with self._lock:
                worker = self._workers.get(name)
            if worker is None:
                continue
            health = worker.health(instant)
            if health.stuck or (not health.running and not worker.is_alive()):
                replaced = self._restart_if_due(name, worker, instant)
                if replaced is not None:
                    health = replaced.health(instant)
            report.append(health)

        return report

    def _restart_if_due(
        self, name: str, worker: HardwareWorker, now: float,
    ) -> HardwareWorker | None:
        with self._lock:
            due_at = self._next_restart_at.get(name, 0.0)
            if now < due_at:
                return None
            count = self._restarts.get(name, 0)
            delay = self._backoff[min(count, len(self._backoff) - 1)]
            self._restarts[name] = count + 1
            self._next_restart_at[name] = now + delay
            factory = self._factories[name]

        limited.warning(
            f"supervisor.{name}",
            f"{name} : thread bloqué ou arrêté — remplacement "
            f"(tentative {count + 1}, prochaine au plus tôt dans {delay:g} s)",
        )

        worker.retire()
        replacement = factory()
        replacement.note_restart(count + 1)
        with self._lock:
            self._workers[name] = replacement
        replacement.start()
        return replacement

    def health(self, now: float | None = None) -> list[WorkerHealth]:
        """Relevé de santé sans effet de bord (aucun redémarrage déclenché)."""
        instant = monotonic() if now is None else now
        with self._lock:
            workers = [self._workers[name] for name in self._order]
        return [worker.health(instant) for worker in workers]


# ---------------------------------------------------------------------------
# Thread des clapets : exécute les ordres et relit l'état
# ---------------------------------------------------------------------------

class ValveWorker(threading.Thread):
    """Seul thread autorisé à toucher aux actionneurs de clapets.

    Il fait deux choses, et jamais depuis le thread graphique :

    * consommer les ordres déposés dans la file de commandes ;
    * relire l'état de chaque clapet et le publier.
    """

    def __init__(
        self,
        drivers: dict[CircuitId, object],
        command_bus: CommandBus,
        slots: dict[CircuitId, LatestValue],
        *,
        poll_period_s: float = 0.5,
    ) -> None:
        super().__init__(name="valve_worker", daemon=True)
        self._drivers = drivers
        self._bus = command_bus
        self._slots = slots
        self._poll_period_s = max(0.05, poll_period_s)
        self._stop_event = threading.Event()
        self._executed = 0

    @property
    def executed(self) -> int:
        return self._executed

    def request_stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:       # pragma: no cover - exercé via les tests d'intégration
        # L'état est relu d'abord : au démarrage, l'écran doit connaître les
        # clapets sans attendre qu'une première commande arrive.
        self._poll_states()
        while not self._stop_event.is_set():
            command = self._bus.get(timeout=self._poll_period_s)
            if command is not None:
                self._execute(command)
                for extra in self._bus.drain():
                    self._execute(extra)
            self._poll_states()

    # ------------------------------------------------------------------
    def _execute(self, command: Command) -> None:
        if not isinstance(command, ManualValveCommand):
            logger.warning("commande ignorée, type inconnu : %r", type(command).__name__)
            return

        driver = self._drivers.get(command.circuit)
        if driver is None:
            limited.warning(
                f"valve.{command.circuit.value}.absent",
                f"clapet {command.circuit.value} : aucun actionneur disponible",
            )
            return

        try:
            if command.action is ValveCommand.OPEN:
                driver.open()
            elif command.action is ValveCommand.CLOSE:
                driver.close()
            else:
                driver.stop()
            self._executed += 1
            limited.clear(f"valve.{command.circuit.value}.error")
            logger.debug("clapet %s : %s exécuté",
                         command.circuit.value, command.action.value)
        except Exception as exc:
            limited.error(
                f"valve.{command.circuit.value}.error",
                f"clapet {command.circuit.value} : {type(exc).__name__}: {exc}",
            )

    def _poll_states(self) -> None:
        for circuit, driver in self._drivers.items():
            slot = self._slots.get(circuit)
            if slot is None:
                continue
            now = monotonic()
            try:
                observation = observe_valve(driver, now)
            except Exception as exc:
                slot.mark_fault(f"{type(exc).__name__}: {exc}", now)
                limited.error(
                    f"valve.{circuit.value}.read",
                    f"clapet {circuit.value} : lecture d'état impossible ({exc})",
                )
            else:
                slot.set(observation, now)


def observe_valve(driver: object, now: float | None = None) -> ValveObservation:
    """Construit une observation de clapet à partir d'un pilote.

    Aucune déduction : ``confirmed`` est ce que le pilote confirme, et rien
    d'autre. C'est ce qui garantit qu'un actionneur sans retour de position ne
    peut pas faire afficher un état présenté comme certain.
    """
    fault = bool(driver.has_fault())
    return ValveObservation(
        commanded=driver.get_commanded_state(),
        confirmed=driver.get_confirmed_state(),
        feedback_available=bool(driver.has_position_feedback()),
        display_state=driver.get_state(),
        fault=fault,
        status=Status.FAULT if fault else Status.OK,
        updated_at=monotonic() if now is None else now,
        reason="actionneur en défaut" if fault else None,
    )
