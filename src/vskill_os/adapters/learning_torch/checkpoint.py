"""Strict loading of local diagnostic candidates, with no runtime promotion."""

import torch

from ...learning.policy_manifest import ActionSchema
from ...learning.ppo_config import PPOConfig
from ..isaac.learning.observations import observation_manifest
from .ppo import ActorCritic


def load_candidate(path, *, expected_schema=None):
    payload = torch.load(path, map_location='cpu', weights_only=True)
    if payload.get('version') != 'vf-ppo-checkpoint/1' or payload.get('policy_kind') != 'diagnostic_candidate':
        raise ValueError('unsupported candidate checkpoint')
    config = PPOConfig.from_dict(payload['config'])
    schema = ActionSchema.from_dict(payload['action_schema'])
    if expected_schema is not None and schema != expected_schema:
        raise ValueError('checkpoint action schema differs from the evaluator configuration')
    if payload['observations'] != observation_manifest(schema.control):
        raise ValueError('checkpoint observation order, privileges or normalization changed')
    active = [schema.control.joint_names.index(n) for n in schema.mask.active]
    if (payload['active_indices'] != active or payload['seed'] != config.seed
            or not 0 <= payload['training_transitions'] <= config.transitions
            or payload.get('training_demonstrations') is not False or payload.get('ik_initialization') is not False):
        raise ValueError('checkpoint has an unsupported action head or training provenance')
    model = ActorCritic(len(payload['observations']['features']), len(active), config.hidden_size, config.initial_log_std)
    model.load_state_dict(payload['model'], strict=True)
    if any(not torch.isfinite(value).all() for value in model.state_dict().values()):
        raise ValueError('nonfinite policy weights')
    model.eval()
    return model, payload, schema, config
