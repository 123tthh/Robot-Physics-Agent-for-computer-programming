"""Backend-independent learning records; no simulator or training dependencies.

The first implementation evaluates synthetic evidence contracts only. It cannot
write verified Runtime state or certify a physical experiment.
"""

from .contracts import EpisodeRecord, TaskSpec
from .evaluation import EvaluationPolicy, EvaluationResult, IndependentTypingEvaluator

__all__ = [
    "EpisodeRecord", "TaskSpec", "EvaluationPolicy", "EvaluationResult",
    "IndependentTypingEvaluator",
]
