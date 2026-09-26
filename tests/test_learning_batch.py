"""Batch isolation contracts. The in-memory fixture is not physical evidence."""

from dataclasses import replace
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest

from vskill_os.learning.batch import BatchConfig,RowState,row_seed
from vskill_os.learning.environment import ActionRejected,JointControlSpec,JointTargetAction,ResetSpec
from vskill_os.learning.physical import CoordinateLimit
from vskill_os.adapters.isaac.learning.config import SingleHandConfig

try:
    import numpy as np
except ImportError:
    np=None


class BatchContractTests(unittest.TestCase):
    def test_frozen_sizes_geometry_and_finite_configs(self):
        for count in (1,16,64):
            config=BatchConfig(num_envs=count)
            self.assertEqual(BatchConfig.from_json(config.to_json()),config)
        for change in (dict(num_envs=2),dict(num_envs=True),dict(spacing_m=2.),dict(spacing_m=float("nan")),
                       dict(control_steps=1),dict(control_steps=10000),dict(gpu_max_rigid_contact_count=0)):
            with self.subTest(change=change),self.assertRaises(ValueError):
                BatchConfig(**change)

    def test_row_seeds_reproduce_across_scales_without_touching_global_rng(self):
        random.seed(77)
        before=random.getstate()
        rows=[RowState(i,ResetSpec(f"env{i}",seed=row_seed(7,i))) for i in range(64)]
        draws=[row.rng.random() for row in rows]
        self.assertEqual(random.getstate(),before)
        self.assertEqual(len(set(draws)),64)
        reference=rows[15].fingerprint()
        rows[0]=RowState(0,ResetSpec("reset0",seed=row_seed(7,0)))
        self.assertEqual(rows[0].rng.random(),draws[0])
        self.assertEqual(rows[15].fingerprint(),reference)


class ArrayFixture:
    """A deterministic tensor-shaped fixture, with no Isaac or Torch import."""
    def __init__(self,directory):
        self.directory=directory
        self.batch=BatchConfig(num_envs=16,chunk_samples=4)
        self.config=SingleHandConfig(settling_steps=1)
        self.control=JointControlSpec(("finger",),(-1.,),(1.,),(.3,),("finger",),1/60)
        self.ids=[0]
        # 'a' is deliberately not the first key: shared sample IDs once caused
        # all subsequent keys in a frame to be discarded by the bridge.
        self.key_names=("backspace","a")
        self.limits=(CoordinateLimit("finger","rad",-1.,1.,8.),)
        self.origins=SimpleNamespace(cpu=lambda:SimpleNamespace(tolist=lambda:[[0.,0.,0.]]*16))
        self.torch=SimpleNamespace(tensor=lambda values,device=None:np.asarray(values,dtype=np.float32))
        self.targets=np.zeros((16,1),dtype=np.float32)
        self.data={key:np.zeros(shape,dtype=np.float32) for key,shape in dict(q=(16,1),v=(16,1),
            targets=(16,1),key_q=(16,2),key_targets=(16,2),forces=(16,2,3),key_xyz=(16,2,3),tip=(16,3),
            pelvis=(16,3),torso=(16,4)).items()}
        self.data["pelvis"][:,2]=.625
        self.data["torso"][:,0]=1.
        self.fixture_pressed=set()

    def snapshot(self):
        self.data["targets"]=self.targets
        return {key:value.copy() for key,value in self.data.items()}

    def advance(self):
        self.data["q"]=self.targets.copy()
        self.data["key_q"][:]=0.
        self.data["forces"][:]=0.
        for index in self.fixture_pressed:
            self.data["key_q"][index,1]=-.003
            self.data["forces"][index,1,2]=1.

    def cache_reset_state(self):
        self.cached=self.snapshot()

    def reset_rows(self,rows):
        for key in self.data:
            self.data[key][rows]=self.cached[key][rows]
        self.targets[rows]=self.cached["targets"][rows]
        self.fixture_pressed.difference_update(rows)


@unittest.skipIf(np is None,"optional learning-batch NumPy adapter")
class BatchIsolationTests(unittest.TestCase):
    def setUp(self):
        from vskill_os.adapters.isaac.learning.batch_env import BatchKeyboardEnv
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.scene=ArrayFixture(Path(self.directory.name))
        self.env=BatchKeyboardEnv(self.scene)
        self.env.settle()
        self.initial=self.env.reset_rows(list(range(16)))
        self.addCleanup(self.env.close)

    def actions(self,delta=0.):
        return tuple(JointTargetAction(self.scene.control.joint_names,(delta,),1/60,lane.tick/240+1/60,
                                      lane.owner,len(lane.actions)) for lane in self.env.lanes)

    def test_last_row_bad_owner_rejects_whole_batch_before_any_write(self):
        before=self.scene.snapshot()
        state=self.env.state()
        actions=list(self.actions(.2))
        actions[-1]=replace(actions[-1],owner_token=self.env.lanes[0].owner)
        with self.assertRaisesRegex(ActionRejected,"CONTROL_OWNERSHIP"):
            self.env.step(actions)
        self.assertEqual(self.env.state(),state)
        self.assertTrue(all(np.array_equal(before[k],self.scene.snapshot()[k]) for k in before))
        self.assertEqual(self.env.global_tick,1)

    def test_text_events_feedback_budget_and_policy_state_are_row_local(self):
        for lane in self.env.lanes:
            self.env.request(lane.index,lane.owner)
            lane.state.policy_state=(float(lane.index),lane.state.rng.random())
        self.scene.fixture_pressed={3}
        observations=self.env.step(self.actions())
        self.assertEqual([i for i,lane in enumerate(self.env.lanes) if lane.events],[3])
        self.assertEqual(self.env.lanes[3].feedback.text_state.history[-1].text,"a")
        self.assertTrue(all(not lane.bridge.rejected_samples for lane in self.env.lanes))
        self.assertTrue(all(not ob.feedback for ob in observations))
        old=self.env.lanes[3]
        state=self.env.state()
        reset=self.env.reset_rows([3])
        self.assertEqual(self.env.global_tick,5)
        self.assertEqual([self.env.state()[i] for i in range(16) if i!=3],[state[i] for i in range(16) if i!=3])
        self.assertFalse(reset[3][0].feedback)
        self.assertEqual(reset[3][0].budget.accepted_requests,0)
        self.assertFalse(self.env.lanes[3].state.policy_state)
        self.assertFalse(self.env.lanes[3].events)
        self.assertTrue(old.closed)
        self.assertEqual(len(old.feedback.close().cancelled_request_ids),1)
        with self.assertRaises(RuntimeError):
            self.env.request(3,old.owner)

    def test_invalid_reset_selection_and_feedback_index_do_not_change_state(self):
        before=self.env.state()
        for rows in ([],[1,1],[-1],[16],[True]):
            with self.subTest(rows=rows),self.assertRaises(ValueError):
                self.env.reset_rows(rows)
        for row in (-1,16,True):
            with self.assertRaises(ValueError):
                self.env.request(row,self.env.lanes[0].owner)
        self.assertEqual(self.env.state(),before)

    def test_independent_reset_checker_detects_tensor_and_hidden_state_crosstalk(self):
        from vskill_os.adapters.isaac.learning.batch_verify import check_reset_isolation
        self.env.step(self.actions())
        before=self.scene.snapshot(); state_before=self.env.state()
        self.env.reset_rows([0])
        after=self.scene.snapshot(); state_after=self.env.state()
        self.assertEqual(check_reset_isolation(before,after,[0],state_before,state_after),[])
        after["q"][15,0]=.1
        self.assertIn("RESET_CHANGED_NEIGHBOR_PHYSICS",check_reset_isolation(before,after,[0],state_before,state_after))
        after=self.scene.snapshot()
        state_after[15]={**state_after[15],"rng_sha256":"forged"}
        self.assertIn("RESET_CHANGED_NEIGHBOR_STATE",check_reset_isolation(before,after,[0],state_before,state_after))

    def test_all_substeps_are_saved_once_in_bounded_chunks(self):
        self.env.step(self.actions())
        self.env.close()
        records=self.env.recorder.files
        self.assertEqual([entry["samples"] for entry in records],[4,1])
        chunks=[]
        for entry in records:
            with np.load(Path(self.directory.name)/entry["path"],allow_pickle=False) as raw:
                chunks.extend(raw["global_tick"].tolist())
                self.assertEqual(raw["q"].shape[1:],(16,1))
        self.assertEqual(chunks,list(range(1,6)))

    def test_safety_failure_retains_the_rejected_raw_state(self):
        self.scene.data["v"][15,0]=9.
        with self.assertRaisesRegex(RuntimeError,"row 15.*JOINT_SPEED"):
            self.env.step(self.actions())
        with np.load(Path(self.directory.name)/"safety_failure.npz",allow_pickle=False) as raw:
            self.assertEqual(float(raw["v"][15,0]),9.)
            self.assertEqual(int(raw["global_tick"]),2)
        self.assertTrue((Path(self.directory.name)/"safety_failure.json").is_file())


if __name__=="__main__":
    unittest.main()
