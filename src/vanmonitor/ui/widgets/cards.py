"""Les cartes de l'écran d'accueil : batterie, réservoirs, températures, chauffage.

Chaque carte sait se redessiner à partir d'une lecture, et rien d'autre. Elles
n'interrogent aucun service et ne détiennent aucun état métier.
"""

from __future__ import annotations

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ...constants import (
    CircuitId,
    HeatingMode,
    Status,
    TankId,
    ValveCommand,
    ValveState,
    ZONE_ORDER,
    ZoneId,
)
from ...models import BatteryReading, CircuitStatus, TankReading, TemperatureReading
from .. import theme
from ..layout_profile import LayoutProfile
from ..theme import Metrics
from .primitives import (
    BarGauge,
    BatteryIcon,
    Card,
    ThermometerIcon,
    ValueLabel,
    french,
    label,
    recolor,
)

#: Libellé affiché quand une valeur n'est pas disponible.
NO_VALUE = "--"


class BatteryCard(Card):
    """Grande valeur d'état de charge, pictogramme, mesures secondaires."""

    def __init__(self, metrics: Metrics, profile: LayoutProfile,
                 parent: QWidget | None = None) -> None:
        super().__init__("Énergie — batterie (SmartShunt)", metrics, parent)
        self._metrics = metrics
        self._profile = profile

        columns = QHBoxLayout()
        columns.setSpacing(metrics.px(10))

        left = QVBoxLayout()
        left.setSpacing(metrics.px(4))
        compact = profile is not LayoutProfile.STANDARD
        self._soc = ValueLabel(
            metrics, theme.GREEN,
            size=metrics.font_big if compact else metrics.font_huge,
        )
        self._icon = BatteryIcon(metrics)
        left.addStretch(1)
        left.addWidget(self._soc)
        if not compact:
            left.addWidget(self._icon)
        left.addStretch(1)

        right = QVBoxLayout()
        right.setSpacing(metrics.px(1))
        right.addStretch(1)
        self._voltage = label(NO_VALUE, size=metrics.font_small, color=theme.TEXT,
                              align=Qt.AlignRight | Qt.AlignVCenter)
        self._current = label(NO_VALUE, size=metrics.font_small, color=theme.TEXT,
                              align=Qt.AlignRight | Qt.AlignVCenter)
        self._power = label(NO_VALUE, size=metrics.font_small, color=theme.TEXT,
                            align=Qt.AlignRight | Qt.AlignVCenter)
        self._consumed = label(NO_VALUE, size=metrics.font_small,
                               color=theme.TEXT_MUTED,
                               align=Qt.AlignRight | Qt.AlignVCenter)
        for widget in (self._voltage, self._current, self._power):
            right.addWidget(widget)
        if profile.shows_secondary_metrics:
            right.addWidget(self._consumed)
        right.addStretch(1)

        columns.addLayout(left, 1)
        columns.addLayout(right, 0)
        self.body().addLayout(columns, 1)

        self._autonomy = label("", size=metrics.font_small, color=theme.TEXT_MUTED,
                               align=Qt.AlignHCenter | Qt.AlignVCenter)
        self.body().addWidget(self._autonomy)
        self._autonomy.setVisible(False)

    def update_reading(self, reading: BatteryReading) -> None:
        if reading.status is not Status.OK:
            self._soc.set_value(NO_VALUE, "", theme.TEXT_DIM)
            self._icon.set_state(None, theme.TEXT_DIM)
            for widget in (self._voltage, self._current, self._power, self._consumed):
                widget.setText(NO_VALUE)
            # Le SmartShunt est au bout d'une liaison, pas d'un capteur : c'est
            # la liaison qu'il faut nommer quand elle tombe.
            self._autonomy.setText(
                "Liaison non intégrée" if reading.status is Status.ABSENT
                else "SmartShunt non joignable"
            )
            recolor(self._autonomy, theme.RED)
            self._autonomy.setVisible(True)
            return

        soc = reading.soc_percent
        color = theme.GREEN if (soc is None or soc >= 20) else theme.RED
        self._soc.set_value(
            NO_VALUE if soc is None else f"{soc:.0f}", "%" if soc is not None else "",
            color,
        )
        self._icon.set_state(None if soc is None else soc / 100.0, color)

        self._voltage.setText(
            NO_VALUE if reading.voltage_v is None else f"{french(reading.voltage_v, 2)} V"
        )
        self._current.setText(
            NO_VALUE if reading.current_a is None
            else f"{french(reading.current_a, 1, signed=True)} A"
        )
        self._power.setText(
            NO_VALUE if reading.power_w is None
            else f"{french(reading.power_w, 0, signed=True)} W"
        )
        self._consumed.setText(
            NO_VALUE if reading.consumed_ah is None
            else f"{french(reading.consumed_ah, 1, signed=True)} Ah"
        )

        # L'autonomie disparaît quand elle n'est pas fournie : pas de « N/A ».
        if reading.time_to_go_min is None or not self._profile.shows_secondary_metrics:
            self._autonomy.setVisible(False)
        else:
            days, remainder = divmod(int(reading.time_to_go_min), 60 * 24)
            hours = remainder // 60
            text = f"{days} j {hours} h" if days else f"{hours} h {remainder % 60:02d}"
            self._autonomy.setText(f"Autonomie : {text}")
            recolor(self._autonomy, theme.TEXT_MUTED)
            self._autonomy.setVisible(True)


class TankCard(Card):
    """Réservoir : valeur principale, valeur secondaire, jauge."""

    def __init__(self, tank: TankId, title: str, metrics: Metrics,
                 profile: LayoutProfile = LayoutProfile.STANDARD,
                 parent: QWidget | None = None) -> None:
        super().__init__(title, metrics, parent)
        self._tank = tank
        self._metrics = metrics
        self._profile = profile
        self._color = theme.TANK_COLORS[tank]

        compact = profile is not LayoutProfile.STANDARD
        self._value = ValueLabel(
            metrics, self._color,
            size=metrics.font_big if compact else metrics.font_huge,
        )
        self._secondary = label("", size=metrics.font_small, color=theme.TEXT_MUTED)

        self.body().addStretch(1)
        self.body().addWidget(self._value)
        self.body().addWidget(self._secondary)
        self.body().addStretch(1)

        # En compact, la jauge cède la place au chiffre : mieux vaut une valeur
        # lisible qu'une valeur tronquée surmontée d'une barre.
        self._gauge = BarGauge(metrics, self._color)
        self._gauge.setVisible(profile.shows_gauges)
        if profile.shows_gauges:
            self.body().addWidget(self._gauge)

    def update_reading(self, reading: TankReading, *, alert: bool = False) -> None:
        if reading.status is Status.FAULT:
            self._value.set_value(NO_VALUE, "", theme.TEXT_DIM)
            self._secondary.setText("Erreur capteur")
            recolor(self._secondary, theme.RED)
            self._gauge.set_state(None, "Erreur capteur")
            return
        if reading.status is not Status.OK:
            self._value.set_value(NO_VALUE, "", theme.TEXT_DIM)
            self._secondary.setText("Capteur absent")
            recolor(self._secondary, theme.TEXT_DIM)
            self._gauge.set_state(None, NO_VALUE)
            return
        if not reading.calibrated:
            self._value.set_value(NO_VALUE, "", theme.TEXT_DIM)
            self._secondary.setText("Non calibré")
            recolor(self._secondary, theme.TEXT_DIM)
            self._gauge.set_state(None, "à calibrer")
            return

        recolor(self._secondary, theme.TEXT_MUTED)
        percent = reading.percent
        ratio = None if percent is None else percent / 100.0
        # En alerte, le chiffre passe au rouge avec la jauge : la couleur porte
        # la même information des deux côtés.
        colour = theme.RED if alert else self._color

        if reading.litres is not None:
            # Litres en grand, pourcentage en secondaire.
            self._value.set_value(f"{reading.litres:.0f}", "L", colour)
            self._secondary.setText(
                NO_VALUE if percent is None else f"{percent:.0f} %"
            )
            capacity = reading.litres / (percent / 100.0) if percent else None
            gauge_text = (f"{reading.litres:.0f} / {capacity:.0f} L"
                          if capacity else f"{reading.litres:.0f} L")
        else:
            self._value.set_value(
                NO_VALUE if percent is None else f"{percent:.0f}", "%", colour
            )
            self._secondary.setText("")
            gauge_text = NO_VALUE if percent is None else f"{percent:.0f} %"

        suffix = "  hors plage" if reading.out_of_range else ""
        self._gauge.set_state(ratio, gauge_text + suffix, alert=alert)


class TemperatureCard(Card):
    """Liste des cinq zones, valeurs alignées à droite."""

    def __init__(self, metrics: Metrics, parent: QWidget | None = None) -> None:
        super().__init__("Températures", metrics, parent)
        self._metrics = metrics
        self._rows: dict[ZoneId, tuple[ThermometerIcon, QLabel, QLabel]] = {}

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(metrics.px(6))
        grid.setVerticalSpacing(metrics.px(1))
        grid.setColumnStretch(1, 1)

        for row, zone in enumerate(ZONE_ORDER):
            dot = ThermometerIcon(metrics)
            name = label("", size=metrics.font_small, color=theme.TEXT)
            value = label(NO_VALUE, size=metrics.font_normal, color=theme.TEXT,
                          bold=True, align=Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(dot, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(value, row, 2)
            self._rows[zone] = (dot, name, value)

        self.body().addLayout(grid)
        self.body().addStretch(1)

    def update_readings(self, readings: dict[ZoneId, TemperatureReading]) -> None:
        for zone, (dot, name, value) in self._rows.items():
            reading = readings.get(zone)
            if reading is None:
                continue
            name.setText(reading.label)

            if reading.status is Status.OK and reading.celsius is not None:
                dot.set_color(theme.BLUE)
                value.setText(f"{french(reading.celsius)} °C")
                recolor(value, theme.TEXT)
            elif reading.status is Status.FAULT:
                dot.set_color(theme.RED)
                value.setText("Erreur capteur")
                recolor(value, theme.RED)
            else:
                dot.set_color(theme.TEXT_DIM)
                value.setText(NO_VALUE)
                recolor(value, theme.TEXT_DIM)


class CircuitTile(Card):
    """Une électrovanne : nom, mode, état, et la mention « commandé » s'il le faut."""

    def __init__(self, metrics: Metrics, profile: LayoutProfile,
                 parent: QWidget | None = None) -> None:
        super().__init__(None, metrics, parent, inner=True)
        self._metrics = metrics
        self._profile = profile

        from .primitives import ValveIndicator

        self._name = label("", size=metrics.font_small, color=theme.TEXT, bold=True,
                           align=Qt.AlignHCenter | Qt.AlignVCenter)
        self._mode = label("", size=metrics.font_tiny, color=theme.TEXT_MUTED,
                           align=Qt.AlignHCenter | Qt.AlignVCenter)
        self._indicator = ValveIndicator(metrics)
        self._state = label(NO_VALUE, size=metrics.font_small, color=theme.TEXT_DIM,
                            bold=True, align=Qt.AlignHCenter | Qt.AlignVCenter)
        # « OUVERTURE COMMANDÉE » passe sur deux lignes plutôt que d'être rogné.
        self._state.setWordWrap(True)
        self._note = label("", size=metrics.font_tiny, color=theme.ORANGE,
                           align=Qt.AlignHCenter | Qt.AlignVCenter)
        self._thresholds = label("", size=metrics.font_tiny, color=theme.TEXT_DIM,
                                 align=Qt.AlignHCenter | Qt.AlignVCenter)

        body = self.body()
        body.setSpacing(metrics.px(2))
        body.addWidget(self._name)
        body.addWidget(self._mode)

        icon_row = QHBoxLayout()
        icon_row.addStretch(1)
        icon_row.addWidget(self._indicator)
        icon_row.addStretch(1)
        body.addLayout(icon_row)

        body.addWidget(self._state)
        body.addWidget(self._note)
        if profile.shows_thresholds:
            body.addWidget(self._thresholds)
        body.addStretch(1)

    def update_status(self, status: CircuitStatus) -> None:
        self._name.setText(status.label)
        self._mode.setText("AUTO" if status.mode is HeatingMode.AUTO else "MANUEL")

        color, filled, crossed, text = _valve_appearance(status)
        self._indicator.set_state(color, filled=filled, crossed=crossed)
        self._state.setText(text)
        recolor(self._state, color)

        if status.fault:
            self._note.setText(status.fault_reason or "défaut")
            recolor(self._note, theme.RED)
        elif not status.feedback_available and status.commanded is ValveCommand.NONE:
            # Aucun ordre encore passé et aucun retour : il faut le dire, l'état
            # affiché ne porte pas encore l'information.
            self._note.setText("sans retour de position")
            recolor(self._note, theme.TEXT_DIM)
        else:
            # Dans tous les autres cas, le libellé d'état dit lui-même s'il
            # s'agit d'une position confirmée ou d'une simple commande.
            self._note.setText("")

        if status.thresholds_defined:
            self._thresholds.setText(
                f"Ouvre ≤ {french(status.open_below_c)} °C\n"
                f"Ferme ≥ {french(status.close_above_c)} °C"
            )
        else:
            self._thresholds.setText("seuils à définir")


def _valve_appearance(status: CircuitStatus) -> tuple[str, bool, bool, str]:
    """Couleur, corps plein ou évidé, croix, libellé — pour un état de clapet.

    Le vocabulaire est strictement séparé, et c'est le point le plus important
    de cet écran :

    * ``OUVERTE`` et ``FERMÉE`` décrivent une **position physique confirmée**
      par un retour de position réel. Ces deux mots ne sont jamais employés
      autrement ;
    * ``OUVERTURE COMMANDÉE`` et ``FERMETURE COMMANDÉE`` décrivent un **ordre
      transmis** dont le matériel n'a rien confirmé. C'est le cas de tout
      actionneur sans retour de position ;
    * ``OUVERTURE`` et ``FERMETURE`` décrivent une course en cours, constatée.

    La forme renforce la distinction : corps de vanne plein pour une position
    confirmée, évidé sinon.
    """
    if status.fault or status.display_state is ValveState.ERREUR:
        return theme.RED, False, True, "DÉFAUT"

    if status.display_state is ValveState.OUVERTURE:
        return theme.AMBER, False, False, "OUVERTURE"
    if status.display_state is ValveState.FERMETURE:
        return theme.AMBER, False, False, "FERMETURE"
    if status.display_state is ValveState.INCONNU:
        return theme.TEXT_DIM, False, False, "INCONNU"

    if status.state_is_certain:
        # Position réellement confirmée : seul cas où l'on emploie OUVERTE/FERMÉE.
        if status.display_state is ValveState.OUVERT:
            return theme.GREEN, True, False, "OUVERTE"
        return theme.TEXT_MUTED, True, False, "FERMÉE"

    # Rien de confirmé : on ne parle plus que de la commande transmise.
    if status.commanded is ValveCommand.OPEN:
        return theme.ORANGE, False, False, "OUVERTURE COMMANDÉE"
    if status.commanded is ValveCommand.CLOSE:
        return theme.ORANGE, False, False, "FERMETURE COMMANDÉE"
    return theme.TEXT_DIM, False, False, "INCONNU"


class HeatingCard(Card):
    """Les trois circuits nommés, côte à côte."""

    def __init__(self, metrics: Metrics, profile: LayoutProfile,
                 circuits: list[CircuitId], parent: QWidget | None = None) -> None:
        super().__init__("Chauffage — électrovannes", metrics, parent)
        row = QHBoxLayout()
        row.setSpacing(metrics.px(6))
        self._tiles: dict[CircuitId, CircuitTile] = {}
        for circuit in circuits:
            tile = CircuitTile(metrics, profile)
            self._tiles[circuit] = tile
            row.addWidget(tile, 1)
        self.body().addLayout(row, 1)

    def update_statuses(self, statuses: dict[CircuitId, CircuitStatus]) -> None:
        for circuit, tile in self._tiles.items():
            status = statuses.get(circuit)
            if status is not None:
                tile.update_status(status)
