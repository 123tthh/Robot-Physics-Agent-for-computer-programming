"""Vector guards owned by the environment, never by the PPO model.

Limits match the previously audited scalar guards; parity is regression tested.
All 69 coordinates and all key contacts are checked at every physics substep.
"""

import math
import torch


SAFETY_CODES = ('INVALID_PHYSICS_SAMPLE', 'COMMANDED_KEYCAP', 'KEY_TRAVEL_LIMIT', 'JOINT_LIMIT',
                'JOINT_SPEED', 'EXCESS_FORCE', 'UNSAFE_SUPPORT', 'UNSAFE_POSTURE')


class TensorSafetyMonitor:
    def __init__(self, limits, origins, physical):
        self.low = origins.new_tensor([l.lower-.03 for l in limits])
        self.high = origins.new_tensor([l.upper+.03 for l in limits])
        self.speed = origins.new_tensor([l.maximum_speed+.05 for l in limits])
        self.origins = origins + origins.new_tensor((0., 0., .625))
        self.physical = physical

    def check(self, raw):
        finite = torch.stack([torch.isfinite(v).flatten(1).all(-1) for v in raw.values()]).all(0)
        q, v, key_q, torso = raw['q'], raw['v'], raw['key_q'], raw['torso']
        codes = torch.stack((~finite, (raw['key_targets'] != 0.).any(-1),
            ((key_q < -.0045) | (key_q > .0005)).any(-1), ((q < self.low) | (q > self.high)).any(-1),
            (v.abs() > self.speed).any(-1), (raw['forces'].norm(dim=-1) > self.physical.maximum_contact_force_n).any(-1),
            (raw['pelvis']-self.origins).norm(dim=-1) > self.physical.support_tolerance_m,
            ((torso.square().sum(-1)-1).abs() > .02) | (1-2*(torso[:, 1].square()+torso[:, 2].square()) < math.cos(.25))), dim=-1)
        return codes


class TensorActionGuard:
    def __init__(self, schema, device):
        self.schema = schema
        spec = schema.control
        self.low = torch.tensor(spec.lower_rad, device=device)
        self.high = torch.tensor(spec.upper_rad, device=device)
        self.rate = torch.tensor(spec.maximum_speed_rad_s, device=device)*spec.control_dt_s
        self.scale = torch.tensor(schema.step_scale_rad, device=device)
        self.mask = torch.tensor([n in schema.mask.active for n in spec.joint_names], device=device)

    def apply(self, targets, action):
        if targets.shape != action.shape or action.ndim != 2 or action.shape[-1] != 23:
            raise ValueError('JOINT_MAPPING')
        if (not torch.isfinite(action).all() or not torch.isfinite(targets).all()
                or (action.abs() > 1).any() or ((targets < self.low-1e-6) | (targets > self.high+1e-6)).any()):
            raise ValueError('ACTION_RANGE_OR_STATE')
        proposed = targets + self.scale*action
        delta = (self.scale*action*self.mask).clamp(-self.rate, self.rate)
        executed = (targets+delta).clamp(self.low, self.high)
        return proposed, executed
