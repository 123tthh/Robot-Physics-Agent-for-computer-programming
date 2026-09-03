"""Built-in reference and mock skills."""

from .mock_computer import build_demo_registry
from .simulation import build_simulation_registry

__all__ = ["build_demo_registry", "build_simulation_registry"]
