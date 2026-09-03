"""Robot-specific profiles kept behind the generic control boundary."""

from .g1 import (
    G1DeploymentConfig,
    G1HardwareProfile,
    G1Joint,
    G1PolicyManifest,
    G1_5010_PROFILES,
)

__all__ = [
    "G1DeploymentConfig",
    "G1HardwareProfile",
    "G1Joint",
    "G1PolicyManifest",
    "G1_5010_PROFILES",
]
