"""Synthetic aggregation contracts, never evidence of physical task success."""

import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from vskill_os.learning.multiseed import MultiseedProtocol, summarize_seed, wilson_reference_interval
from vskill_os.learning.ppo_config import PPOConfig
from vskill_os.adapters.isaac.learning.multiseed_runner import aggregate
from vskill_os.adapters.isaac.learning.isolated_worker import asset_mirror, healthy_gpu, kit_arguments, same_cuda_uuid


class MultiseedTests(unittest.TestCase):
    def setUp(self):
        self.protocol = MultiseedProtocol()

    def evaluation(self, count=100):
        return dict(evidence_valid=True, success_count=count, episode_count=100, training_transitions=1_000_000,
            cases=[dict(seed=seed, evidence_valid=True, physical_primitive_passed=i<count,
                        task_failures=[] if i<count else ['INCOMPLETE_RELEASE'], elapsed_sim_s=1.1 if i<count else 3.)
                   for i, seed in enumerate(self.protocol.evaluation_seeds)])

    def report(self, seed, successes=100):
        return dict(training_seed=seed, accepted=successes >= 95, protocol=self.protocol.to_dict(), recording='synthetic-test-only',
            training=dict(training_valid=True, summary=dict(transitions=1_000_000,
                config=self.protocol.training_config(PPOConfig(), seed).to_dict())),
            evaluation=self.evaluation(successes))

    def test_frozen_protocol_and_hyperparameters(self):
        self.assertEqual(MultiseedProtocol.from_json(self.protocol.to_json()), self.protocol)
        config = self.protocol.training_config(PPOConfig(), 17)
        self.assertEqual(config.seed, 17)
        self.assertEqual(len(config.evaluation_seeds), 100)
        with self.assertRaises(ValueError):
            self.protocol.training_config(replace(PPOConfig(), learning_rate=.001), 17)
        for values in ({'training_seeds': (7, 7, 29)}, {'evaluation_seeds': (2001,)*100},
                       {'minimum_success_rate': .9}, {'transitions_per_seed': 64}):
            with self.assertRaises(ValueError):
                replace(self.protocol, **values)

    def test_each_seed_must_pass_even_if_pooled_rate_is_high(self):
        reports = [self.report(s, n) for s, n in zip(self.protocol.training_seeds, (94, 100, 100))]
        result = aggregate(self.protocol, reports)
        self.assertFalse(result['accepted'])
        self.assertEqual(result['minimum_seed_success_rate'], .94)
        self.assertEqual(result['seeds'][0]['failure_codes'], {'INCOMPLETE_RELEASE': 6})
        reports[0] = self.report(7, 95)
        self.assertTrue(aggregate(self.protocol, reports)['accepted'])

    def test_missing_duplicate_or_reordered_training_seeds_rejected(self):
        reports = [self.report(s) for s in self.protocol.training_seeds]
        for changed in (reports[:-1], [reports[0], reports[0], reports[2]], list(reversed(reports))):
            with self.assertRaises(ValueError):
                aggregate(self.protocol, changed)

    def test_invalid_evidence_missing_cases_and_wrong_checkpoint_fail(self):
        evaluation = self.evaluation()
        for mutation in ('invalid', 'missing', 'wrong_seed', 'counter', 'budget'):
            changed = copy.deepcopy(evaluation)
            if mutation == 'invalid': changed['cases'][0]['evidence_valid'] = False
            if mutation == 'missing': changed['cases'].pop()
            if mutation == 'wrong_seed': changed['cases'][0]['seed'] = 9999
            if mutation == 'counter': changed['success_count'] = 99
            if mutation == 'budget': changed['training_transitions'] = 500_000
            with self.subTest(mutation=mutation):
                self.assertFalse(summarize_seed(self.protocol, 7, changed)['passed'])

    def test_aggregate_rejects_changed_hyperparameters_and_unverified_training(self):
        reports = [self.report(s) for s in self.protocol.training_seeds]
        reports[1]['training']['summary']['config']['epochs'] = 8
        with self.assertRaises(ValueError): aggregate(self.protocol, reports)
        reports[1] = self.report(17)
        reports[1]['training']['training_valid'] = False
        with self.assertRaises(ValueError): aggregate(self.protocol, reports)

    def test_interval_endpoints_do_not_turn_perfect_repeats_into_certainty(self):
        low, high = wilson_reference_interval(100, 100)
        self.assertAlmostEqual(low, .9630065, places=6)
        self.assertAlmostEqual(high, 1.)
        low_zero, high_zero = wilson_reference_interval(0, 100)
        self.assertAlmostEqual(low_zero, 0.)
        self.assertAlmostEqual(high_zero, 1-low)
        with self.assertRaises(ValueError): wilson_reference_interval(101, 100)
        with self.assertRaises(ValueError): wilson_reference_interval(0, 0)

    def test_launch_rejects_bad_health_and_ambiguous_gpu_selection(self):
        uuid = 'GPU-649639b6-99c5-a691-551c-b92df950205b'
        self.assertEqual(healthy_gpu(uuid, f'{uuid}, 0, Test GPU, 0')['physical_index'], 0)
        for visible, row in ((uuid, f'{uuid}, 6, Test GPU, 2762'),
                (uuid, f'{uuid}, 0, Test GPU, Unknown Error'),
                ('0', f'{uuid}, 0, Test GPU, 0'),
                (uuid+','+uuid, f'{uuid}, 0, Test GPU, 0'),
                (uuid, f'{uuid}, 0, Test GPU, 0\n{uuid}, 1, Test GPU, 0')):
            with self.subTest(visible=visible, row=row):
                with self.assertRaises(ValueError): healthy_gpu(visible, row)

    def test_torch_raw_uuid_matches_smi_prefix_but_not_a_different_card(self):
        raw = '649639b6-99c5-a691-551c-b92df950205b'
        self.assertTrue(same_cuda_uuid('GPU-'+raw, raw))
        self.assertTrue(same_cuda_uuid('GPU-'+raw, 'GPU-'+raw))
        self.assertFalse(same_cuda_uuid('GPU-'+raw, '8e253e57-aad0-06e2-5ef7-114283a2e092'))

    def test_offline_asset_mirror_rejects_changed_files_and_path_escape(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'assets'
            root.mkdir()
            asset = root/'ground.usd'
            asset.write_bytes(b'synthetic asset fixture')
            item = dict(path='ground.usd', sha256=hashlib.sha256(asset.read_bytes()).hexdigest())
            manifest = Path(temporary)/'manifest.json'
            def write():
                manifest.write_text(json.dumps(dict(version='vf-isaac-asset-mirror/1', files=[item])))
            write()
            receipt = asset_mirror(root, manifest)
            self.assertIn('--/persistent/isaac/asset_root/cloud='+str(root), kit_arguments(root/'kit', receipt))
            asset.write_bytes(b'changed geometry')
            with self.assertRaises(ValueError): asset_mirror(root, manifest)
            outside = Path(temporary)/'outside.usd'
            outside.write_bytes(b'synthetic asset fixture')
            item['path'] = '../outside.usd'
            write()
            with self.assertRaises(ValueError): asset_mirror(root, manifest)
            self.assertIsNone(asset_mirror(None))


if __name__ == '__main__':
    unittest.main()
