"""Fabrique du matériel — le seul endroit qui choisit entre réel et simulé.

C'est ici, et nulle part ailleurs, que le programme décide s'il parle à un
fourgon ou à un fourgon virtuel. Passer de l'un à l'autre ne demande aucune
autre modification : ``--sim`` en ligne de commande, ou ``general.simulation``
dans la configuration.

``core/`` et ``ui/`` n'importent jamais ce module : c'est ``app.py``, le point
d'assemblage, qui l'appelle et distribue le résultat.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import ConfigStore
from ..constants import CIRCUIT_ORDER, CircuitId, TANK_ORDER, TankId, ZONE_ORDER, ZoneId
from ..util.logging_setup import get_logger
from .interfaces import LevelSensor, SmartShuntInterface, TemperatureSensor, ValveDriver

logger = get_logger("hal.factory")


@dataclass
class HalBundle:
    """Tout le matériel de l'installation, réel ou simulé.

    ``temperature_sensors`` peut contenir ``None`` : une zone dont la sonde
    n'est pas associée est un état normal, pas une panne. L'écran affichera
    ``--`` et le programme fonctionnera sans elle.
    """

    simulation: bool
    temperature_sensors: dict[ZoneId, TemperatureSensor | None] = field(default_factory=dict)
    level_sensors: dict[TankId, LevelSensor | None] = field(default_factory=dict)
    smartshunt: SmartShuntInterface | None = None
    valves: dict[CircuitId, ValveDriver] = field(default_factory=dict)
    #: Présent uniquement en simulation ; ``None`` sur le matériel réel.
    sim_state: object | None = None

    # ------------------------------------------------------------------
    # Le bus 1-Wire vit : on y branche et on y débranche des sondes.
    # ------------------------------------------------------------------
    def scan_temperature_sensor_ids(self) -> list[str]:
        """Identifiants réellement présents sur le bus, à cet instant.

        Ne lève jamais : un bus absent rend une liste vide, ce qui est la
        vérité et non une panne.
        """
        if self.simulation:
            return list(self.sim_state.present_sensor_ids())     # type: ignore[union-attr]
        from .real.ds18b20 import scan_sensor_ids
        return scan_sensor_ids()

    def rebuild_temperature_sensor(
        self, config: ConfigStore, zone: ZoneId,
    ) -> TemperatureSensor | None:
        """Reconstruit la sonde d'une zone après changement d'association.

        Appelé quand l'utilisateur réassocie une sonde depuis les Paramètres :
        sans cela, la nouvelle association ne prendrait effet qu'au prochain
        démarrage.
        """
        sensor = build_temperature_sensor(
            config, zone, simulation=self.simulation, sim_state=self.sim_state,
        )
        self.temperature_sensors[zone] = sensor
        return sensor

    def sensor_for_id(
        self, config: ConfigStore, sensor_id: str,
    ) -> TemperatureSensor | None:
        """Sonde correspondant à un identifiant, associée ou non à une zone.

        Sert à l'identification physique : on doit pouvoir lire une sonde
        détectée sur le bus avant de décider à quelle zone elle appartient.
        """
        timeout = float(config.get("temperatures.read_timeout_s", 3.0))
        if self.simulation:
            from .sim.sim_state import SIM_SENSOR_IDS
            from .sim.mock_temperature import MockTemperatureSensor
            for zone, simulated_id in SIM_SENSOR_IDS.items():
                if simulated_id == sensor_id:
                    return MockTemperatureSensor(
                        zone, self.sim_state, timeout_s=timeout,   # type: ignore[arg-type]
                    )
            return None
        try:
            from .real.ds18b20 import DS18B20Sensor
            return DS18B20Sensor(sensor_id, timeout_s=timeout)
        except Exception as exc:
            logger.warning("sonde %s : construction impossible (%s)", sensor_id, exc)
            return None


def build_temperature_sensor(
    config: ConfigStore,
    zone: ZoneId,
    *,
    simulation: bool,
    sim_state: object | None = None,
) -> TemperatureSensor | None:
    """Construit la sonde d'une zone, ou ``None`` si la zone n'en a pas.

    Une zone sans sonde associée est un état normal, pas une panne : l'écran
    affichera ``--`` et tout le reste continuera de fonctionner.
    """
    configured_id = config.get(f"temperatures.zones.{zone.value}.sensor_id")
    timeout = float(config.get("temperatures.read_timeout_s", 3.0))

    if simulation:
        from .sim.mock_temperature import MockTemperatureSensor
        from .sim.sim_state import SIM_SENSOR_IDS

        # Une sonde mesure la température de l'endroit où elle est posée, pas
        # de la zone à laquelle le logiciel l'attribue. Associer la sonde du
        # coffre à la cabine doit donc afficher la température du coffre : sans
        # cela, une erreur d'association resterait invisible et la page Sondes
        # ne servirait à rien.
        target_id = configured_id or SIM_SENSOR_IDS[zone]
        physical_zone = (sim_state.zone_of_sensor(target_id)      # type: ignore[union-attr]
                         if sim_state is not None else None)
        if physical_zone is None:
            logger.info("simulation : zone %s associée à %s, sonde absente du bus",
                        zone.value, configured_id)
            return None
        if configured_id is None:
            # Confort : en simulation, une zone non associée l'est d'office,
            # sans que rien ne soit écrit dans la configuration.
            logger.debug("simulation : association automatique %s → %s",
                         zone.value, target_id)
        return MockTemperatureSensor(physical_zone, sim_state, timeout_s=timeout)

    if not configured_id:
        return None
    try:
        from .real.ds18b20 import DS18B20Sensor
        return DS18B20Sensor(configured_id, timeout_s=timeout)
    except Exception as exc:        # un pilote fautif ne bloque pas les autres
        logger.error("sonde %s : construction impossible (%s)", configured_id, exc)
        return None


def build_hal(config: ConfigStore, *, simulation: bool) -> HalBundle:
    """Construit tout le matériel décrit par la configuration."""
    if simulation:
        return _build_simulated(config)
    return _build_real(config)


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def _build_simulated(config: ConfigStore) -> HalBundle:
    from .sim.mock_level import MockLevelSensor
    from .sim.mock_smartshunt import MockSmartShuntInterface
    from .sim.mock_valve import MockValveDriver
    from .sim.sim_state import SimState

    sim_state = SimState()
    bundle = HalBundle(simulation=True, sim_state=sim_state)

    for zone in ZONE_ORDER:
        bundle.temperature_sensors[zone] = build_temperature_sensor(
            config, zone, simulation=True, sim_state=sim_state,
        )

    level_timeout = float(config.get("tanks.read_timeout_s", 1.0))
    for tank in TANK_ORDER:
        bundle.level_sensors[tank] = MockLevelSensor(
            tank, sim_state, timeout_s=level_timeout,
        )

    bundle.smartshunt = MockSmartShuntInterface(
        sim_state, timeout_s=float(config.get("battery.read_timeout_s", 2.0)),
    )

    valve_timeout = float(config.get("heating.command_timeout_s", 5.0))
    for circuit in CIRCUIT_ORDER:
        bundle.valves[circuit] = MockValveDriver(
            circuit, sim_state, timeout_s=valve_timeout,
        )

    logger.info("matériel simulé construit (%d sondes, %d niveaux, %d clapets)",
                sum(1 for sensor in bundle.temperature_sensors.values() if sensor),
                len(bundle.level_sensors), len(bundle.valves))
    return bundle


# ---------------------------------------------------------------------------
# Matériel réel
# ---------------------------------------------------------------------------

def _build_real(config: ConfigStore) -> HalBundle:
    """Construit le matériel réel, dans la mesure où il est déjà défini.

    Une famille non encore intégrée n'empêche pas les autres de fonctionner :
    elle est simplement absente du lot, et l'écran affichera ``--``.
    """
    bundle = HalBundle(simulation=False)

    for zone in ZONE_ORDER:
        bundle.temperature_sensors[zone] = build_temperature_sensor(
            config, zone, simulation=False,
        )

    for tank in TANK_ORDER:
        bundle.level_sensors[tank] = _try_build(
            f"niveau {tank.value}",
            lambda tank=tank: _build_level_sensor(config, tank),
        )

    bundle.smartshunt = _try_build("SmartShunt", lambda: _build_smartshunt(config))

    for circuit in CIRCUIT_ORDER:
        valve = _try_build(
            f"clapet {circuit.value}",
            lambda circuit=circuit: _build_valve(config, circuit),
        )
        if valve is not None:
            bundle.valves[circuit] = valve

    return bundle


def _try_build(label: str, builder):
    """Construit un équipement, ou journalise une fois et rend ``None``."""
    try:
        return builder()
    except NotImplementedError as exc:
        logger.warning("%s indisponible — %s", label, exc)
    except Exception as exc:        # un pilote fautif ne bloque pas les autres
        logger.error("%s : construction impossible (%s)", label, exc)
    return None


def _build_level_sensor(config: ConfigStore, tank: TankId):
    from .real.adc_level import ADCLevelSensor, RealADC
    adc = RealADC()
    return ADCLevelSensor(
        adc,
        str(config.get(f"tanks.{tank.value}.channel", "")),
        timeout_s=float(config.get("tanks.read_timeout_s", 1.0)),
    )


def _build_smartshunt(config: ConfigStore):
    link = config.section("battery.link")
    if link.get("type") != "vedirect_serial":
        raise NotImplementedError(
            f"type de liaison SmartShunt inconnu : {link.get('type')!r}"
        )
    from .real.smartshunt_vedirect import VeDirectSmartShunt
    return VeDirectSmartShunt(
        link.get("port"),
        baudrate=int(link.get("baudrate", 19200)),
        timeout_s=float(config.get("battery.read_timeout_s", 2.0)),
    )


def _build_valve(config: ConfigStore, circuit: CircuitId):
    driver = config.section(f"heating.circuits.{circuit.value}.driver")
    if driver.get("type") == "mock":
        raise NotImplementedError(
            "aucun actionneur réel configuré (H-3 : MATERIEL À INTEGRER PLUS TARD)"
        )
    from .real.valve_driver import RealValveDriver
    return RealValveDriver(**driver.get("params", {}))
