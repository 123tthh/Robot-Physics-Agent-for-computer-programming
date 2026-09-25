"""Optional, simulation-only adapter for NVIDIA's pinned G1 balance policy.

This is not the Stay Seated policy and is not approved for hardware or Dex5.
Its compatibility is established only by separately verified simulation runs.
"""
from __future__ import annotations

from collections import deque
import hashlib
from pathlib import Path

from vskill_os.robot.g1 import G1Joint

BODY_NAMES = tuple(j.name.lower()+"_joint" for j in G1Joint)
LOWER_NAMES = BODY_NAMES[:15]
DEFAULT_LOWER = (-.1,0.,0.,.3,-.2,0.)*2+(0.,0.,0.)
KP = (150.,150.,150.,200.,40.,40.)*2+(250.,250.,250.)
KD = (2.,2.,2.,4.,2.,2.)*2+(5.,5.,5.)
POLICY_SHA256 = "f645da599d4ca3d29ed273c8f4712620bb680d34977469ca3aeabe5bb9631c18"
POLICY_URL = "https://media.githubusercontent.com/media/NVlabs/GR00T-WholeBodyControl/2a5e6a1b55d9d0205a96e757b86007ec020cf589/decoupled_wbc/sim2mujoco/resources/robots/g1/policy/GR00T-WholeBodyControl-Balance.onnx"


class BalancePolicy:
    def __init__(self, path: Path):
        import numpy as np
        import onnxruntime as ort
        if hashlib.sha256(path.read_bytes()).hexdigest() != POLICY_SHA256:
            raise ValueError("balance policy fingerprint mismatch")
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path),sess_options=options,providers=["CPUExecutionProvider"])
        if self.session.get_inputs()[0].shape[-1] != 516 or self.session.get_outputs()[0].shape[-1] != 15:
            raise ValueError("balance policy observation/action contract mismatch")
        self.history = deque([np.zeros(86,dtype=np.float32) for _ in range(6)],maxlen=6)
        self.action = np.zeros(15,dtype=np.float32)
        self.default = np.array(DEFAULT_LOWER+(0.,)*14,dtype=np.float32)

    def target(self, *, position, velocity, angular_velocity_body, gravity_body,
               height: float, pitch: float = 0., command=(0.,0.,0.)):
        np = self.np
        sample = np.concatenate((np.array(command)*[2.,2.,.5], [height,0.,pitch,0.],
            np.array(angular_velocity_body)*.5, gravity_body,
            np.array(position)-self.default,np.array(velocity)*.05,self.action)).astype(np.float32)
        if sample.shape != (86,) or not np.isfinite(sample).all():
            raise ValueError("invalid proprioceptive balance observation")
        self.history.append(sample)
        self.action = self.session.run(None,{self.session.get_inputs()[0].name:np.concatenate(self.history)[None,:]})[0][0]
        if self.action.shape != (15,) or not np.isfinite(self.action).all():
            raise ValueError("invalid balance policy action")
        return np.array(DEFAULT_LOWER)+.25*self.action
