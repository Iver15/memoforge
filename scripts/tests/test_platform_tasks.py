"""Native defaults and task affinity must not change Claude tasks."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memoforge import modes, task
from memoforge.host_profile import require_task_platform


class PlatformTasksTest(unittest.TestCase):
    def test_native_defaults_are_russian(self):
        for platform in ('codex','zcode'):
            with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':platform}, clear=True):
                values=task.resolve_options(path=Path('/missing/options.json'))['values']
                self.assertEqual([values[k] for k in ('memo_language','ui_language','dashboard','writer_model')], ['ru','ru','false','inherit'])

    def test_explicit_language_wins(self):
        with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':'codex'}, clear=True):
            self.assertEqual(task.resolve_options({'memo_language':'en'},path='/missing/options.json')['values']['memo_language'],'en')

    def test_model_inherits_host(self):
        with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':'codex'}, clear=True):
            config=modes.resolve_config(None, {'writer_model':'inherit'})
            self.assertEqual(config['writer_model'],'inherit')
            self.assertNotIn('writer_model_fallback',config)

    def test_other_platform_cannot_resume(self):
        with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':'codex'}, clear=True):
            with self.assertRaisesRegex(ValueError,'platform_mismatch'):
                require_task_platform({'runtime_platform':'zcode'})

    def test_legacy_task_is_claude(self):
        with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':'codex'}, clear=True):
            with self.assertRaisesRegex(ValueError,'platform_mismatch'):
                require_task_platform({})
        with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':'claude'}, clear=True):
            require_task_platform({})

    def test_native_model_is_validated(self):
        with patch.dict(os.environ, {'MEMOFORGE_PLATFORM':'zcode'}, clear=True):
            self.assertIsNone(task.validate_option('writer_model','inherit'))
            self.assertIsNotNone(task.validate_option('writer_model','opus'))

    def test_native_task_records_profile_and_rejects_foreign_next(self):
        import argparse
        import tempfile
        from memoforge import machine, state_io
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            env={'MEMOFORGE_PLATFORM':'codex','HOME':tmp,'PLUGIN_DATA':tmp+'/data'}
            with patch.dict(os.environ,env,clear=True):
                parser=__import__('memoforge.cli',fromlist=['build_parser']).build_parser()
                args=parser.parse_args(['task','new','--query','Краткая проверка','--output-folder',str(root/'out')])
                result=args.func(args)
                work=Path(result['work_dir'])
                state=state_io.read_state(work)
                self.assertEqual(state['runtime_platform'],'codex')
                self.assertEqual((state['language'],state['ui_language']),('ru','ru'))
                snapshot=(work/'state.json').read_bytes()
            with patch.dict(os.environ,dict(env,MEMOFORGE_PLATFORM='zcode'),clear=True):
                with self.assertRaisesRegex(ValueError,'platform_mismatch'):
                    machine.run_next(argparse.Namespace(workdir=str(work)))
            self.assertEqual((work/'state.json').read_bytes(),snapshot)
