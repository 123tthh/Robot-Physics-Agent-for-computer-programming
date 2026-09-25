import copy
from dataclasses import dataclass
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import types

from vskill_os.adapters.isaac.standing_scene import load_configuration, asset_evidence, main, ROOT
from vskill_os.adapters.isaac.runtime_guard import RuntimeCheck


class StandingSceneConfigTests(unittest.TestCase):
    def test_standing_only_and_no_runtime_guard_bypass(self):
        base=json.loads((ROOT/'configs/simulation/dex3_standing_demo.json').read_text())
        for section,key,value in [('scene','chair',True),('robot','base_mode','seated'),
                                  ('runtime','refuse_busy_gpu',False),('scene','physics_dt_s',float('nan'))]:
            altered=copy.deepcopy(base); altered[section][key]=value
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'config.json'; path.write_text(json.dumps(altered))
                with self.assertRaises(ValueError): load_configuration(path)

    def test_asset_tampering_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            usd=Path(tmp)/'robot.usd'; usd.write_text('modified')
            manifest=Path(tmp)/'manifest.json'; manifest.write_text(json.dumps({'usd_target':{'sha256':'bad'}}))
            with self.assertRaises(ValueError): asset_evidence(usd,manifest)

    def test_unchanged_root_cannot_hide_modified_physics_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            usd=Path(tmp)/'robot.usd'; usd.write_text('root')
            physics=Path(tmp)/'physics.usd'; physics.write_text('original physics')
            digest=lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
            manifest=Path(tmp)/'manifest.json'
            manifest.write_text(json.dumps({'usd_target':{'sha256':digest(usd)}}))
            lock=Path(tmp)/'lock.json'
            lock.write_text(json.dumps({'layers':[{'path':p.name,'sha256':digest(p)} for p in (usd,physics)]}))
            self.assertTrue(asset_evidence(usd,manifest,lock)['dependencies_verified'])
            physics.write_text('changed inertias')
            with self.assertRaisesRegex(ValueError,'dependency'):
                asset_evidence(usd,manifest,lock)

    def test_blocked_launch_writes_honest_report_without_importing_isaac(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'blocked.json'
            with patch('vskill_os.adapters.isaac.standing_scene.asset_evidence', return_value={}), \
                 patch('vskill_os.adapters.isaac.standing_scene.RuntimeLease.__enter__',side_effect=RuntimeError('BUSY')):
                status=main(['--output',str(path)])
            report=json.loads(path.read_text())
            self.assertEqual(status,2)
            self.assertFalse(report['simulation_started'])
            self.assertFalse(report['physical_acceptance'])
            self.assertEqual(report['status'],'blocked_before_launch')

    def test_shutdown_errors_still_close_kit_and_write_failure(self):
        @dataclass
        class Frame:
            tick: int = 0

        for failing in ('driver', 'kit'):
            calls=[]

            class App:
                def __init__(self, config): pass
                def close(self, **kwargs):
                    calls.append('kit')
                    if failing == 'kit': raise RuntimeError('kit close error')

            class Driver:
                dt=1/120
                tick=0
                def __init__(self, *args, **kwargs): pass
                def observe(self): return Frame()
                def close(self):
                    calls.append('driver')
                    if failing == 'driver': raise RuntimeError('driver close error')

            modules={'isaacsim':types.SimpleNamespace(SimulationApp=App),
                     'vskill_os.adapters.isaac.standing_driver':types.SimpleNamespace(Isaac6StandingDriver=Driver)}
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'failed.json'
                with patch.dict('sys.modules',modules), \
                     patch('vskill_os.adapters.isaac.standing_scene.asset_evidence',return_value={}), \
                     patch('vskill_os.adapters.isaac.standing_scene.RuntimeLease.__enter__',return_value=RuntimeCheck('/sim','6.0.1',True,True,(),())):
                    status=main(['--inspect-only','--output',str(path)])
                report=json.loads(path.read_text())
                self.assertNotEqual(status,0)
                self.assertEqual(calls,['driver','kit'])
                self.assertEqual(report['status'],'failed')
                self.assertFalse(report['physical_acceptance'])

    def test_final_evidence_is_on_disk_before_kit_fast_exit(self):
        @dataclass
        class Frame:
            tick: int = 30

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'inspected.json'
            observed = []

            class App:
                def __init__(self, config):
                    self.asserted_fast_shutdown = config['fast_shutdown']
                def close(self, *, exit_code):
                    # Isaac can call os._exit here; no finally would run.
                    observed.append((exit_code, json.loads(path.read_text())))

            class Driver:
                dt, tick = 1/120, 30
                def __init__(self, *args, **kwargs): pass
                def observe(self): return Frame()
                def close(self): pass

            modules = {'isaacsim': types.SimpleNamespace(SimulationApp=App),
                'vskill_os.adapters.isaac.standing_driver': types.SimpleNamespace(Isaac6StandingDriver=Driver)}
            with patch.dict('sys.modules', modules), \
                 patch('vskill_os.adapters.isaac.standing_scene.asset_evidence', return_value={}), \
                 patch('vskill_os.adapters.isaac.standing_scene.RuntimeLease.__enter__',
                       return_value=RuntimeCheck('/sim','6.0.1',True,True,(),())):
                self.assertEqual(main(['--inspect-only', '--output', str(path)]), 0)
            code, report = observed[0]
            self.assertEqual(code, 0)
            self.assertEqual(report['status'], 'scene_inspected_only')
            self.assertEqual(report['physics_steps'], 30)
            self.assertTrue(report['driver_close_completed'])
            self.assertEqual(report['last_phase'], 'kit_shutdown_requested')
            self.assertFalse(report['physical_acceptance'])
