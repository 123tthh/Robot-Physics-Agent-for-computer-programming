"""Standard-library batch contracts and row-local random/policy state."""

from dataclasses import dataclass
import hashlib
import random

from .environment import ResetSpec
from .serialization import JsonRecord


@dataclass(frozen=True, slots=True)
class BatchConfig(JsonRecord):
    num_envs: int = 1
    spacing_m: float = 3.
    seed: int = 7
    control_steps: int = 144
    chunk_samples: int = 64
    gpu_max_rigid_contact_count: int = 2**23
    gpu_max_rigid_patch_count: int = 5*2**15
    gpu_found_lost_pairs_capacity: int = 2**21
    gpu_found_lost_aggregate_pairs_capacity: int = 2**25
    gpu_total_aggregate_pairs_capacity: int = 2**21
    gpu_collision_stack_size: int = 2**26

    def __post_init__(self):
        JsonRecord.__post_init__(self)
        if (self.num_envs not in (1, 16, 64) or self.spacing_m < 3 or self.seed < 0
                or not 136 <= self.control_steps <= 156 or self.chunk_samples < 1
                or min(self.gpu_max_rigid_contact_count, self.gpu_max_rigid_patch_count,
                       self.gpu_found_lost_pairs_capacity, self.gpu_found_lost_aggregate_pairs_capacity,
                       self.gpu_total_aggregate_pairs_capacity, self.gpu_collision_stack_size) < 1):
            raise ValueError("invalid batch size, geometry, diagnostic duration or buffer capacity")


def row_seed(seed: int, index: int) -> int:
    """Stable across batch sizes; resetting a neighbor never advances this RNG."""
    return int.from_bytes(hashlib.sha256(f"vf04:{seed}:{index}".encode()).digest()[:8], "big") % 2**63


class RowState:
    def __init__(self, index: int, reset: ResetSpec):
        self.index, self.reset_spec = index, reset
        self.rng = random.Random(reset.seed)
        # Explicit policy-state buffer, not a claim of a trained recurrent model.
        self.policy_state: tuple[float, ...] = ()

    def fingerprint(self) -> dict:
        return dict(index=self.index, episode_id=self.reset_spec.episode_id, seed=self.reset_spec.seed,
                    rng_sha256=hashlib.sha256(repr(self.rng.getstate()).encode()).hexdigest(),
                    policy_state=self.policy_state)


def diagnostic_profile(index: int) -> str:
    if index % 2:
        return "idle"
    if index % 4 == 2:
        return "software_drop"
    if index % 8 == 4:
        return "missing_readout"
    return "press"
