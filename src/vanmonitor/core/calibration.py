"""Calibration multipoints : valeur brute d'un capteur → litres ou pourcentage.

Le réservoir d'eau propre est de forme irrégulière (passage de roue) : la
hauteur mesurée n'est pas proportionnelle au volume. D'où une table de points
relevés au remplissage, et une interpolation linéaire entre eux.

Deux règles qui comptent autant que le calcul lui-même :

* **aucune extrapolation.** Au-delà du dernier point, la valeur est bornée et
  signalée « hors plage ». Mieux vaut avouer qu'on ne sait pas que d'inventer
  un volume ;
* **aucune table incohérente enregistrée.** Une saisie non monotone est
  refusée avec un message affichable à l'écran, et la table précédente reste
  active.

Étape 5 : filtrage des mesures brutes (médian + moyenne exponentielle) et
assistant de calibration complet. La table elle-même est finie.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

UNIT_LITRES = "litres"
UNIT_PERCENT = "percent"


@dataclass(frozen=True)
class CalibrationPoint:
    """Un relevé : la valeur brute lue, et le contenu réel correspondant."""

    raw: float
    value: float        # litres, ou pourcentage selon l'unité du réservoir


class CalibrationError(ValueError):
    """Table de calibration inutilisable. Le message est destiné à l'écran."""


class CalibrationTable:
    """Table de conversion d'un réservoir, triée et validée."""

    def __init__(
        self,
        points: list[CalibrationPoint] | None = None,
        unit: str = UNIT_LITRES,
        capacity_l: float | None = None,
    ) -> None:
        if unit not in (UNIT_LITRES, UNIT_PERCENT):
            raise CalibrationError(f"unité inconnue : {unit!r}")
        self.unit = unit
        self.capacity_l = capacity_l
        self.points: list[CalibrationPoint] = sorted(
            points or [], key=lambda point: point.raw
        )

    # ------------------------------------------------------------------
    # Sérialisation
    # ------------------------------------------------------------------
    @classmethod
    def from_config(cls, data: dict[str, Any]) -> "CalibrationTable":
        """Construit une table depuis la configuration, sans jamais lever.

        Une table stockée illisible donne une table vide : le réservoir
        affichera ``--``, ce qui est exact, plutôt que d'empêcher le démarrage.
        """
        raw_points = []
        for item in (data.get("calibration") or {}).get("points") or []:
            try:
                raw_points.append(
                    CalibrationPoint(float(item["raw"]), float(item["value"]))
                )
            except (KeyError, TypeError, ValueError):
                continue
        return cls(
            raw_points,
            unit=data.get("unit", UNIT_LITRES),
            capacity_l=data.get("capacity_l"),
        )

    def to_config(self) -> dict[str, Any]:
        return {
            "points": [
                {"raw": point.raw, "value": point.value} for point in self.points
            ],
        }

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------
    def validate(self) -> None:
        """Lève ``CalibrationError`` avec un message lisible par le conducteur."""
        if len(self.points) < 2:
            raise CalibrationError(
                "Il faut au moins deux points pour calibrer un réservoir."
            )

        raws = [point.raw for point in self.points]
        if len(set(raws)) != len(raws):
            raise CalibrationError(
                "Deux points ont la même mesure brute : supprimez-en un."
            )

        values = [point.value for point in self.points]
        increasing = all(b > a for a, b in zip(values, values[1:]))
        decreasing = all(b < a for a, b in zip(values, values[1:]))
        if not (increasing or decreasing):
            raise CalibrationError(
                "Les valeurs doivent toujours monter, ou toujours descendre, "
                "quand la mesure brute augmente."
            )

        if self.unit == UNIT_PERCENT:
            if any(not 0.0 <= value <= 100.0 for value in values):
                raise CalibrationError(
                    "Les pourcentages doivent être compris entre 0 et 100."
                )
        else:
            if any(value < 0.0 for value in values):
                raise CalibrationError("Un volume ne peut pas être négatif.")
            if self.capacity_l is not None and any(
                value > self.capacity_l + 1e-9 for value in values
            ):
                raise CalibrationError(
                    f"Un point dépasse la capacité déclarée "
                    f"({self.capacity_l:g} L)."
                )

    def is_valid(self) -> bool:
        try:
            self.validate()
        except CalibrationError:
            return False
        return True

    def is_calibrated(self) -> bool:
        return self.is_valid()

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------
    def effective_capacity(self) -> float | None:
        """Capacité utilisée pour le pourcentage.

        Celle déclarée si elle existe ; sinon, pour un réservoir calibré en
        litres, le plus haut point de la table — le dernier relevé correspond
        au réservoir plein. ``None`` si rien ne permet de conclure.
        """
        if self.capacity_l is not None:
            return float(self.capacity_l)
        if self.unit == UNIT_LITRES and self.points:
            return max(point.value for point in self.points)
        return None

    def convert(self, raw: float) -> tuple[float, bool]:
        """Interpolation linéaire par segments. Retourne ``(valeur, hors_plage)``."""
        if not self.is_valid():
            raise CalibrationError("Table de calibration incomplète ou incohérente.")

        points = self.points
        if raw <= points[0].raw:
            return points[0].value, raw < points[0].raw
        if raw >= points[-1].raw:
            return points[-1].value, raw > points[-1].raw

        for low, high in zip(points, points[1:]):
            if low.raw <= raw <= high.raw:
                span = high.raw - low.raw
                if span <= 0:
                    return low.value, False
                ratio = (raw - low.raw) / span
                return low.value + ratio * (high.value - low.value), False
        return points[-1].value, True       # inatteignable, filet de sécurité

    def percent(self, raw: float) -> tuple[float | None, bool]:
        """Pourcentage de remplissage, ou ``None`` si la capacité est inconnue."""
        value, out_of_range = self.convert(raw)
        if self.unit == UNIT_PERCENT:
            return max(0.0, min(100.0, value)), out_of_range

        capacity = self.effective_capacity()
        if not capacity:
            return None, out_of_range
        return max(0.0, min(100.0, value / capacity * 100.0)), out_of_range

    def litres(self, raw: float) -> tuple[float | None, bool]:
        """Volume en litres, ou ``None`` pour un réservoir calibré en pourcentage."""
        if self.unit == UNIT_PERCENT:
            return None, False
        return self.convert(raw)

    # ------------------------------------------------------------------
    # Édition
    # ------------------------------------------------------------------
    def with_point(self, raw: float, value: float) -> "CalibrationTable":
        """Nouvelle table avec ce point ajouté (ou remplacé s'il existe déjà)."""
        points = [point for point in self.points if abs(point.raw - raw) > 1e-9]
        points.append(CalibrationPoint(float(raw), float(value)))
        return CalibrationTable(points, self.unit, self.capacity_l)

    def without_point(self, raw: float) -> "CalibrationTable":
        return CalibrationTable(
            [point for point in self.points if abs(point.raw - raw) > 1e-9],
            self.unit,
            self.capacity_l,
        )

    def cleared(self) -> "CalibrationTable":
        return CalibrationTable([], self.unit, self.capacity_l)

    def __len__(self) -> int:
        return len(self.points)


#: Table de démonstration utilisée en simulation quand rien n'est calibré, pour
#: que l'écran montre des valeurs plausibles. Jamais écrite dans la
#: configuration, et jamais utilisée sur le matériel réel.
def demo_table(unit: str, capacity_l: float | None) -> CalibrationTable:
    top = 100.0 if unit == UNIT_PERCENT else (capacity_l or 100.0)
    return CalibrationTable(
        [
            CalibrationPoint(0.00, 0.0),
            CalibrationPoint(0.25, top * 0.20),
            CalibrationPoint(0.50, top * 0.45),
            CalibrationPoint(0.75, top * 0.74),
            CalibrationPoint(1.00, top),
        ],
        unit,
        capacity_l,
    )
