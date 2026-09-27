"""Real subprocess routing with synthetic evidence, never a physical acceptance test."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from vskill_os.adapters.isaac.learning import multiseed_replay as replay
from vskill_os.learning.multiseed import MultiseedProtocol


FIXTURE_RUNNER = '''import argparse, hashlib, json
from pathlib import Path
p = argparse.ArgumentParser()
for name in ('seed', 'run', 'report', 'protocol', 'baseline', 'physics-config', 'action-scale'):
    p.add_argument('--'+name)
p.add_argument('--verify-only', action='store_true')
a = p.parse_args()
root = Path(__file__).resolve().parents[5]
expected = json.loads((Path(a.run)/'manifest.json').read_text())
valid = hashlib.sha256((root/'converted.usd').read_bytes()).hexdigest() == expected['usd_sha256']
result = dict(version='vf-single-key-multiseed-seed/1', accepted=valid,
    training_seed=int(a.seed), recording=str(Path(a.run).resolve()),
    evaluator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    workspace=str(root), findings=[] if valid else ['ASSET_HASH'])
Path(a.report).write_text(json.dumps(result))
raise SystemExit(0 if valid else 1)
'''


class WorkspaceReplayTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.protocol = MultiseedProtocol()
        self.inputs = {}
        for name in ('protocol', 'baseline', 'physics-config', 'action-scale'):
            path = self.root/(name+'.json')
            path.write_text('{}')
            self.inputs[name] = path
        self.directories = []
        for seed in self.protocol.training_seeds:
            workspace = self.root/f'workspace-{seed}'
            for name in replay.REPLAY_SOURCES:
                path = workspace/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(FIXTURE_RUNNER if name == replay.REPLAY_SOURCES[0] else '# fixture\n')
            for package in ('vskill_os', 'vskill_os/adapters', 'vskill_os/adapters/isaac',
                            'vskill_os/adapters/isaac/learning'):
                (workspace/'src'/package/'__init__.py').touch()
            asset = workspace/'converted.usd'
            asset.write_text(f'distinct synthetic converted asset for seed {seed}')
            directory = workspace/'runs'/'final'
            directory.mkdir(parents=True)
            (directory/'manifest.json').write_text(json.dumps({'usd_sha256': replay.sha(asset)}))
            (directory/'multiseed.json').write_text('original report must survive')
            placement = dict(seed=seed, code_root=str(workspace),
                protocol_sha256=replay.sha(self.inputs['protocol']), baseline_sha256=replay.sha(self.inputs['baseline']))
            directory.with_suffix('.placement.json').write_text(json.dumps(placement))
            self.directories.append(directory)
        self.coordinator = self.directories[0].parents[1]
        self.destination = self.root/'fresh'
        self.destination.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(replay, 'ROOT', self.coordinator).start()
        patch.dict(os.environ, {'PYTHONPATH': str(self.coordinator/'src')}).start()

    def one(self, index=1, timeout_s=10):
        return replay.replay_seed(self.directories[index], self.protocol.training_seeds[index],
            inputs=self.inputs, destination=self.destination, timeout_s=timeout_s)

    def test_each_process_uses_own_code_root_and_distinct_assets(self):
        reports, receipts = replay.replay_runs(self.directories, protocol=self.protocol, inputs=self.inputs,
            report_path=self.root/'suite.json', timeout_s=10, workers=3)
        self.assertTrue(all(r['accepted'] for r in reports))
        self.assertEqual([r['workspace'] for r in reports], [str(p.parents[1]) for p in self.directories])
        for directory, receipt in zip(self.directories, receipts, strict=True):
            self.assertTrue(receipt['completed'])
            self.assertEqual(receipt['report_sha256'], replay.sha(Path(receipt['report'])))
            self.assertEqual((directory/'multiseed.json').read_text(), 'original report must survive')

    def test_changed_workspace_asset_still_fails(self):
        (self.directories[1].parents[1]/'converted.usd').write_text('tampered asset')
        report, receipt = self.one()
        self.assertTrue(receipt['completed'])
        self.assertEqual(receipt['returncode'], 1)
        self.assertFalse(report['accepted'])
        self.assertEqual(report['findings'], ['ASSET_HASH'])

    def test_wrong_seed_root_protocol_or_baseline_rejected_before_launch(self):
        path = self.directories[1].with_suffix('.placement.json')
        original = json.loads(path.read_text())
        for key, value in (('seed', 7), ('code_root', str(self.coordinator)),
                           ('protocol_sha256', 'wrong'), ('baseline_sha256', 'wrong')):
            path.write_text(json.dumps(dict(original, **{key: value})))
            with self.subTest(key=key), patch.object(replay.subprocess, 'run') as launch:
                report, receipt = self.one()
                self.assertIsNone(report)
                self.assertIn('WORKSPACE_BINDING', receipt['error'])
                launch.assert_not_called()

    def test_changed_verifier_rejected_before_launch(self):
        (self.directories[1].parents[1]/replay.REPLAY_SOURCES[2]).write_text('# different verifier')
        with patch.object(replay.subprocess, 'run') as launch:
            report, receipt = self.one()
            self.assertIsNone(report)
            self.assertIn('VERIFIER_SOURCE_HASH', receipt['error'])
            launch.assert_not_called()

    def test_timeout_or_missing_fresh_report_never_uses_old_success(self):
        for index, side_effect in ((0, subprocess.TimeoutExpired('fixture', 1)), (1, None)):
            with self.subTest(index=index), patch.object(replay.subprocess, 'run', side_effect=side_effect,
                                                        return_value=subprocess.CompletedProcess([], 0)):
                report, receipt = self.one(index)
                self.assertIsNone(report)
                self.assertFalse(receipt['completed'])
                self.assertIn('error', receipt)

    def test_report_identity_and_returncode_must_match(self):
        mutations = ({'training_seed': 7}, {'recording': 'other-run'}, {'evaluator_sha256': 'changed'},
                     {'accepted': 1}, {'version': 'unknown'}, {})
        parent = self.destination
        for index, mutation in enumerate(mutations):
            self.destination = parent/str(index)
            self.destination.mkdir()
            def false_report(command, **kwargs):
                report = dict(version='vf-single-key-multiseed-seed/1', accepted=True, training_seed=17,
                    recording=str(self.directories[1]), evaluator_sha256=replay.sha(self.coordinator/replay.REPLAY_SOURCES[0]))
                report.update(mutation)
                Path(command[command.index('--report')+1]).write_text(json.dumps(report))
                return subprocess.CompletedProcess(command, 0 if mutation else 1)
            with self.subTest(mutation=mutation), patch.object(replay.subprocess, 'run', side_effect=false_report):
                report, receipt = self.one()
                self.assertIsNone(report)
                self.assertIn('REPLAY_REPORT_BINDING', receipt['error'])

    def test_replay_outputs_cannot_be_reused(self):
        path = self.root/'suite.json'
        (self.root/'suite.replays').mkdir()
        with self.assertRaises(FileExistsError):
            replay.replay_runs(self.directories, protocol=self.protocol, inputs=self.inputs,
                               report_path=path, timeout_s=10, workers=3)
        path.write_text('{"accepted": true}')
        with self.assertRaises(ValueError):
            replay.replay_runs(self.directories, protocol=self.protocol, inputs=self.inputs,
                               report_path=path, timeout_s=10, workers=3)


if __name__ == '__main__':
    unittest.main()
