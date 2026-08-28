"""Rendu texte d'un instantané d'acquisition.

Volontairement sans Qt : ce module sert aussi bien au panneau de simulation
qu'au mode ``--headless``, qui doit fonctionner sur une machine sans interface
graphique.

Il applique déjà les règles d'affichage arrêtées à l'étape 1, afin qu'elles
soient éprouvées avant même d'avoir un écran :

* ``--`` pour une valeur absente ou périmée ;
* ``Erreur capteur`` pour une valeur en défaut ;
* pour un clapet, l'état n'est jamais présenté comme certain sans retour de
  position : il est alors suivi de la mention « commandé ».

(Fichier ajouté par rapport à l'arborescence de l'étape 1 : il évite que le
mode sans interface dépende de PyQt5.)
"""

from __future__ import annotations

from ..constants import (
    CIRCUIT_ORDER,
    ConfirmedState,
    Status,
    TANK_ORDER,
    ValveCommand,
    ZONE_ORDER,
)
from ..models import AcquisitionSnapshot, Sample, ValveObservation

STATUS_LABELS: dict[Status, str] = {
    Status.OK: "OK",
    Status.STALE: "PÉRIMÉ",
    Status.FAULT: "ERREUR",
    Status.ABSENT: "ABSENT",
}

ZONE_LABELS = {
    "local_batterie": "Local batterie",
    "local_eau": "Local eau",
    "coffre": "Coffre",
    "cabine": "Cabine",
    "cellule": "Cellule",
}

TANK_LABELS = {
    "eau_propre": "Eau propre",
    "eaux_grises": "Eaux grises",
    "gasoil": "Gasoil",
}

CIRCUIT_LABELS = {
    "local_eau": "Local eau",
    "local_batterie": "Local batterie",
    "cabine": "Cabine",
}

COMMAND_LABELS = {
    ValveCommand.OPEN: "OUVRIR",
    ValveCommand.CLOSE: "FERMER",
    ValveCommand.STOP: "STOP",
    ValveCommand.NONE: "—",
}


def fr(number: float, decimals: int = 1, *, signed: bool = False) -> str:
    """Nombre à la française : virgule décimale, comme sur l'écran du fourgon."""
    text = f"{number:{'+' if signed else ''}.{decimals}f}"
    return text.replace(".", ",")


def _value_text(sample: Sample, unit: str = "", decimals: int = 1) -> str:
    """Applique la règle d'affichage : ``--`` ou ``Erreur capteur``."""
    if sample.status is Status.FAULT:
        return "Erreur capteur"
    if sample.status is not Status.OK or sample.value is None:
        return "--"
    return f"{fr(float(sample.value), decimals)}{unit}"


def _age_text(sample: Sample) -> str:
    if sample.age_s is None:
        return ""
    return f"il y a {fr(sample.age_s)} s"


def _detail(sample: Sample) -> str:
    if sample.status is Status.OK:
        return _age_text(sample)
    return sample.reason or ""


def format_valve(sample: Sample | None) -> str:
    """Une ligne de clapet, avec la distinction commandé / confirmé."""
    if sample is None or sample.status is Status.ABSENT:
        reason = (sample.reason if sample else None) or "actionneur non intégré"
        return f"{'--':<12}  {'':<28}  {reason}"

    observation: ValveObservation | None = sample.value
    if observation is None:
        return f"{'--':<12}  {'':<28}  pas encore lu"

    commanded = COMMAND_LABELS.get(observation.commanded, "?")
    if observation.fault:
        shown = "ERREUR"
    else:
        shown = observation.display_state.value.upper()

    if observation.state_is_certain:
        certainty = "confirmé par le matériel"
        confirmed = observation.confirmed.value.upper()
    else:
        certainty = "commandé — non confirmé"
        confirmed = (
            "INCONNU" if observation.confirmed is ConfirmedState.INCONNU
            else observation.confirmed.value.upper()
        )
        if not observation.feedback_available:
            certainty = "commandé — aucun retour de position"

    return f"{shown:<11} cmd {commanded:<7} conf {confirmed:<8} {certainty}"


def format_snapshot(snapshot: AcquisitionSnapshot) -> str:
    """Rendu complet, monospace, d'un instantané d'acquisition."""
    lines: list[str] = []
    mode = "SIMULATION" if snapshot.simulation else "MATÉRIEL RÉEL"
    lines.append(f"── ACQUISITION ── {mode} ── t = {snapshot.timestamp:.1f} s ──")
    lines.append("")

    lines.append("TEMPÉRATURES")
    for zone in ZONE_ORDER:
        sample = snapshot.temperatures.get(zone)
        if sample is None:
            continue
        label = ZONE_LABELS[zone.value]
        lines.append(
            f"  {label:<16} {_value_text(sample, ' °C'):>14}   "
            f"{STATUS_LABELS[sample.status]:<7} {_detail(sample)}"
        )
    lines.append("")

    lines.append("NIVEAUX  (valeurs brutes — la calibration arrive à l'étape 5)")
    for tank in TANK_ORDER:
        sample = snapshot.levels.get(tank)
        if sample is None:
            continue
        label = TANK_LABELS[tank.value]
        lines.append(
            f"  {label:<16} {_value_text(sample, '', 3):>14}   "
            f"{STATUS_LABELS[sample.status]:<7} {_detail(sample)}"
        )
    lines.append("")

    lines.append("BATTERIE")
    battery = snapshot.battery
    if battery.status is Status.OK and battery.value is not None:
        reading = battery.value
        parts = []
        if reading.soc_percent is not None:
            parts.append(f"{fr(reading.soc_percent, 0)} %")
        if reading.voltage_v is not None:
            parts.append(f"{fr(reading.voltage_v, 2)} V")
        if reading.current_a is not None:
            parts.append(f"{fr(reading.current_a, 1, signed=True)} A")
        if reading.power_w is not None:
            parts.append(f"{fr(reading.power_w, 0, signed=True)} W")
        if reading.consumed_ah is not None:
            parts.append(f"{fr(reading.consumed_ah, 1, signed=True)} Ah")
        # L'autonomie n'apparaît que si elle est fournie : jamais de « N/A ».
        if reading.time_to_go_min is not None:
            hours, minutes = divmod(int(reading.time_to_go_min), 60)
            parts.append(f"autonomie {hours} h {minutes:02d}")
        lines.append(f"  {' · '.join(parts)}")
        lines.append(f"  {'OK':<7} {_age_text(battery)}")
    else:
        label = "Erreur capteur" if battery.status is Status.FAULT else "--"
        lines.append(f"  {label}")
        lines.append(
            f"  {STATUS_LABELS[battery.status]:<7} {battery.reason or ''}"
        )
    lines.append("")

    lines.append("CHAUFFAGE  (état commandé ≠ état confirmé)")
    for circuit in CIRCUIT_ORDER:
        label = CIRCUIT_LABELS[circuit.value]
        lines.append(f"  {label:<16} {format_valve(snapshot.valves.get(circuit))}")
    lines.append("")

    lines.append("THREADS D'ACQUISITION")
    if not snapshot.workers:
        lines.append("  (aucun)")
    for health in snapshot.workers:
        state = "BLOQUÉ" if health.stuck else ("actif" if health.running else "arrêté")
        detail = []
        if health.consecutive_failures:
            detail.append(f"{health.consecutive_failures} échec(s) d'affilée")
        if health.restarts:
            detail.append(f"{health.restarts} redémarrage(s)")
        if health.last_error:
            detail.append(health.last_error)
        lines.append(f"  {health.name:<16} {state:<8} {' · '.join(detail)}")

    return "\n".join(lines)
