"""Standing Dex3-1 residual training without mandatory simulator imports."""

from .contracts import (
    CartesianResidual, ContactEvidence, DomainRandomization, KeyEvent,
    Observation, TrainingConfig, TypingEnvironment, TypingGoal,
)
from .curriculum import goal_from_text
from .learning import ResidualPolicy, evaluate, load_checkpoint, run_episode, train

__all__ = [
    "CartesianResidual", "ContactEvidence", "DomainRandomization", "KeyEvent",
    "Observation", "ResidualPolicy", "TrainingConfig", "TypingEnvironment",
    "TypingGoal", "evaluate", "goal_from_text", "load_checkpoint", "run_episode", "train",
]
