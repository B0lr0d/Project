"""Écran d'accueil.

Tout ce qui compte tient sans défilement et sans interaction : quatre cartes en
haut, températures et chauffage en dessous. C'est la seule page que le
conducteur regarde en roulant.

L'écran ne connaît aucun matériel : il reçoit un instantané déjà calculé et le
dessine. Il ne suppose jamais un état, il n'affiche que ce qu'on lui donne.
"""

from __future__ import annotations

from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..constants import CIRCUIT_ORDER, TANK_ORDER, TankId
from ..models import SystemSnapshot
from .layout_profile import LayoutProfile
from .theme import Metrics
from .widgets.cards import BatteryCard, HeatingCard, TankCard, TemperatureCard


class HomePage(QWidget):
    """Composition de l'écran d'accueil."""

    def __init__(self, metrics: Metrics, profile: LayoutProfile,
                 tank_titles: dict[TankId, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._metrics = metrics
        self._profile = profile

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(metrics.gap)

        # --- ligne du haut : batterie + trois réservoirs ------------------
        self._battery = BatteryCard(metrics, profile)
        self._tanks = {
            tank: TankCard(tank, tank_titles.get(tank, tank.value), metrics, profile)
            for tank in TANK_ORDER
        }

        if profile is LayoutProfile.STANDARD:
            top = QHBoxLayout()
            top.setSpacing(metrics.gap)
            top.addWidget(self._battery, 32)
            for tank in TANK_ORDER:
                top.addWidget(self._tanks[tank], 22)
            layout.addLayout(top, 40)
        else:
            # Compact : deux rangées de deux, rien n'est retiré.
            grid = QGridLayout()
            grid.setSpacing(metrics.gap)
            grid.addWidget(self._battery, 0, 0)
            grid.addWidget(self._tanks[TankId.EAU_PROPRE], 0, 1)
            grid.addWidget(self._tanks[TankId.EAUX_GRISES], 1, 0)
            grid.addWidget(self._tanks[TankId.GASOIL], 1, 1)
            layout.addLayout(grid, 55)

        # --- températures et chauffage ------------------------------------
        self._temperatures = TemperatureCard(metrics)
        self._heating = HeatingCard(metrics, profile, list(CIRCUIT_ORDER))

        middle = QHBoxLayout()
        middle.setSpacing(metrics.gap)
        middle.addWidget(self._temperatures, 33)
        middle.addWidget(self._heating, 67)
        layout.addLayout(middle, 45)

    # ------------------------------------------------------------------
    def update_snapshot(self, snapshot: SystemSnapshot) -> None:
        """Redessine l'écran à partir d'un instantané. Aucune I/O ici."""
        self._battery.update_reading(snapshot.battery)

        alert_keys = {alert.key for alert in snapshot.alerts}
        for tank, card in self._tanks.items():
            reading = snapshot.tanks.get(tank)
            if reading is None:
                continue
            alert = any(key.startswith(tank.value) for key in alert_keys)
            card.update_reading(reading, alert=alert)

        self._temperatures.update_readings(snapshot.temperatures)
        self._heating.update_statuses(snapshot.circuits)
