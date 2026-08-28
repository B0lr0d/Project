"""Matériel simulé : le « fourgon virtuel ».

Ces classes implémentent exactement les mêmes interfaces que les pilotes réels,
et savent en plus **tomber en panne** de toutes les façons prévues. C'est ce
qui évite que le simulateur mente (risque R-17) : si l'application se comporte
correctement ici, y compris capteurs débranchés et clapets sans retour de
position, elle se comportera correctement dans le fourgon.
"""

from .mock_level import MockLevelSensor
from .mock_smartshunt import MockSmartShuntInterface
from .mock_temperature import MockTemperatureSensor
from .mock_valve import MockValveDriver
from .sim_state import FAULT_LABELS, FaultMode, SIM_SENSOR_IDS, SimState

__all__ = [
    "FAULT_LABELS",
    "FaultMode",
    "MockLevelSensor",
    "MockSmartShuntInterface",
    "MockTemperatureSensor",
    "MockValveDriver",
    "SIM_SENSOR_IDS",
    "SimState",
]
