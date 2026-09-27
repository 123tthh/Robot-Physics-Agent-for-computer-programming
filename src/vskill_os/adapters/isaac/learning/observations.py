"""Privileged single-key teacher features; no text buffer or verifier input."""

from ....learning.ppo_config import NORMALIZATION, teacher_feature_names


def teacher_observation(q, velocity, executed_targets, previous_action, tip, key_xyz,
                        travel, force, pressed_seen, clear_age, elapsed, control, horizon_s=3.):
    """Torch tensors on CPU or GPU; named fixed physical scaling is frozen.

    Physical press history comes from the declared contact/travel sensors.
    There are no learned normalization statistics or final-success features.
    """
    import torch
    low = q.new_tensor(control.lower_rad)
    high = q.new_tensor(control.upper_rad)
    contact = (force.norm(dim=-1) > .01).float()
    return torch.cat((2*(q-low)/(high-low)-1, velocity/8.,
        2*(executed_targets-low)/(high-low)-1, previous_action,
        (tip-key_xyz)/.05, travel[:, None]/.004,
        force.norm(dim=-1, keepdim=True)/12., contact[:, None],
        pressed_seen.float()[:, None], clear_age.clamp(0., .01)[:, None]/.01,
        (1-elapsed/horizon_s).clamp(0., 1.)[:, None], torch.ones_like(travel[:, None])), dim=-1)


def observation_manifest(control):
    return dict(version='vf-single-key-teacher/1', features=list(teacher_feature_names(control.joint_names)),
                normalization=NORMALIZATION, source='privileged_physx_and_physical_history',
                text_buffer=False, verifier_result=False, visual_policy=False)
