"""Fenêtre principale : bandeau, page courante, alertes, navigation.

L'interface ne détient aucune référence vers le matériel. Elle lit l'instantané
publié par la boucle de contrôle à une cadence fixe, et dépose ses commandes
dans la file. Aucune entrée-sortie ne peut donc avoir lieu dans le thread
graphique.
"""

from __future__ import annotations

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from ..constants import TANK_ORDER
from ..models import SystemSnapshot
from . import theme
from .home_page import HomePage
from .layout_profile import profile_for
from .settings_page import SettingsPage
from .theme import metrics_for
from .widgets.chrome import AlertBar, NavBar, TopBar


class MainWindow(QWidget):
    """Assemble les deux écrans et les bandeaux communs."""

    def __init__(self, application, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._app = application

        size = application.screen_size
        self._metrics = metrics_for(
            *size,
            application.config.get("general.screen_diagonal_in"),
        )
        self._profile = profile_for(*size)
        self.resize(*size)
        self.setMinimumSize(400, 240)
        self.setWindowTitle("Monitoring Van")
        self.setStyleSheet(theme.stylesheet(self._metrics))

        metrics = self._metrics
        layout = QVBoxLayout(self)
        layout.setContentsMargins(metrics.margin, metrics.margin,
                                  metrics.margin, metrics.margin)
        layout.setSpacing(metrics.gap)

        self._top = TopBar(metrics)
        layout.addWidget(self._top)

        builder = application.builder
        tank_titles = {tank: builder.tanks.label(tank) for tank in TANK_ORDER}

        self._home = HomePage(metrics, self._profile, tank_titles)
        self._settings = SettingsPage(
            application.config, builder.heating, builder.tanks,
            application.command_bus, application.sensor_ids,
            metrics, self._profile, application.implicit_bindings,
        )

        self._stack = QStackedWidget()
        self._stack.addWidget(self._home)
        self._stack.addWidget(self._settings)
        layout.addWidget(self._stack, 1)

        self._alerts = AlertBar(metrics)
        layout.addWidget(self._alerts)

        self._nav = NavBar(metrics)
        self._nav.navigate.connect(self._navigate)
        layout.addWidget(self._nav)

        hertz = float(application.config.get("general.ui_refresh_hz", 2)) or 2.0
        self._timer = QTimer(self)
        self._timer.setInterval(int(1000 / hertz))
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    # ------------------------------------------------------------------
    def _navigate(self, key: str) -> None:
        self._stack.setCurrentWidget(self._home if key == "home" else self._settings)
        self._nav.set_current(key)
        self.refresh()

    def refresh(self) -> None:
        """Redessine à partir du dernier instantané publié. Aucune I/O ici."""
        snapshot: SystemSnapshot | None = self._app.state.get()
        if snapshot is None:
            return

        self._top.refresh(simulation=snapshot.simulation)
        self._alerts.update_alerts(snapshot.alerts)
        if self._stack.currentWidget() is self._home:
            self._home.update_snapshot(snapshot)
        else:
            self._settings.update_snapshot(snapshot)

    def show_settings(self, section: str | None = None) -> None:
        """Ouvre les Paramètres, éventuellement sur une section précise."""
        self._navigate("settings")
        if section:
            self._settings.show_section(section)

    def closeEvent(self, event) -> None:        # noqa: N802 (API Qt)
        self._timer.stop()
        super().closeEvent(event)
