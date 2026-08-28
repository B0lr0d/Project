"""Configuration : valeurs par défaut, validation, persistance."""

from .defaults import CONFIG_VERSION, DEFAULTS, default_config
from .schema import validate
from .store import ConfigStore

__all__ = ["CONFIG_VERSION", "DEFAULTS", "default_config", "validate", "ConfigStore"]
