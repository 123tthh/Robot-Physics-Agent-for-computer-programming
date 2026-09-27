"""Vector training progress and bounded rewards, separate from verification."""

import torch


class PressProgress:
    def __init__(self, count, key_count, target_index, device):
        self.target_index = target_index
        self.down = torch.zeros((count, key_count), dtype=torch.bool, device=device)
        self.releasing = torch.zeros_like(self.down)
        self.press_count = torch.zeros_like(self.down, dtype=torch.long)
        self.clear_age = torch.zeros_like(self.down, dtype=torch.float)
        self.elapsed = torch.zeros(count, device=device)
        self.ticks = torch.zeros(count, dtype=torch.long, device=device)
        self.last_event_age = torch.full_like(self.clear_age, 1e6)
        self.completed = torch.zeros(count, dtype=torch.bool, device=device)

    def reset(self, rows):
        for field in (self.down, self.releasing, self.press_count, self.clear_age, self.elapsed, self.ticks, self.completed):
            field[rows] = 0
        self.last_event_age[rows] = 1e6

    def advance(self, travel, force, dt):
        contact = force.norm(dim=-1) > .01
        clear = (~contact) & (travel <= .0012)
        was_releasing = self.releasing.clone()
        self.last_event_age += dt
        up = self.down & (travel <= .0012)
        press = (~self.down) & (~was_releasing) & contact & (travel >= .002) & (self.last_event_age >= .008)
        # Enforce physical clearance before rearming, as in the keyboard bridge.
        self.down = (self.down | press) & (~up)
        self.releasing = (was_releasing | up) & contact
        self.press_count += press.long()
        self.last_event_age[up | press] = 0.
        self.clear_age = torch.where(clear, self.clear_age+dt, 0.)
        self.ticks += 1
        self.elapsed = self.ticks.float()*dt
        target_count = self.press_count[:, self.target_index]
        extra = self.press_count.sum(-1)-target_count
        mistakes = (extra > 0) | (target_count > 1)
        complete = (target_count == 1) & (~self.down.any(-1)) & (self.clear_age >= .01-1e-8).all(-1) & (~mistakes)
        pulse = complete & (~self.completed)
        self.completed |= complete
        return pulse, mistakes

    @property
    def pressed_seen(self):
        return self.press_count[:, self.target_index] > 0


def potential(tip, key_xyz, travel, progress):
    distance = (tip-key_xyz).norm(dim=-1)
    approach = (1-distance/.03).clamp(0., 1.)
    before = .25*approach + .75*(travel/.002).clamp(0., 1.)
    after = 1. + .5*(1-travel/.002).clamp(0., 1.) + .5*(progress.clear_age[:, progress.target_index]/.01).clamp(0., 1.)
    return torch.where(progress.pressed_seen, after, before)


def reward_terms(spec, previous_potential, current_potential, completed, mistaken, unsafe,
                 action, previous_action, force, duration, gamma):
    terminal = completed | mistaken | unsafe
    shaped_next = torch.where(terminal, 0., current_potential)
    return torch.stack((spec.completion*completed.float(), spec.potential_scale*(gamma*shaped_next-previous_potential),
        -spec.time_per_s*duration, -spec.action_change*(action-previous_action).square().sum(-1),
        -spec.contact_per_s*(force.norm(dim=-1).amax(-1)-8.).clamp(min=0.)*duration,
        -spec.mistake*mistaken.float(), -spec.safety*unsafe.float()), dim=-1)


REWARD_NAMES = ('completed_cycle', 'potential', 'time', 'action_change', 'excess_contact', 'mistake', 'safety')
