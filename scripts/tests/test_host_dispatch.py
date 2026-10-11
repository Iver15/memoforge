"""Native delegates receive both the legal role and the actual task."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts'))
from memoforge.host_profile import get_profile
from memoforge.host_dispatch import adapt_agent


class HostDispatchTest(unittest.TestCase):
    def payload(self):
        return {'slot':'statutes','agent':'legal-researcher','model':'opus','subagent_type':'memoforge:legal-researcher','description':'Find statutes','prompt':'DYNAMIC TASK /staging/out.json','expected_outputs':[{'work_path':'/staging/out.json'}]}

    def test_claude_payload_is_unchanged(self):
        with patch.dict(os.environ,{'MEMOFORGE_PLATFORM':'claude'}):
            original=self.payload()
            self.assertEqual(adapt_agent(original,get_profile(ROOT),ROOT),original)

    def test_native_prompt_includes_role_and_task(self):
        role=(ROOT/'agents/legal-researcher.md').read_text().split('---',2)[2].strip()
        for platform in ('codex','zcode'):
            with patch.dict(os.environ,{'MEMOFORGE_PLATFORM':platform}):
                result=adapt_agent(self.payload(),get_profile(ROOT),ROOT)
                self.assertIn(role,result['prompt'])
                self.assertIn('DYNAMIC TASK /staging/out.json',result['prompt'])
                self.assertEqual(result['slot'],'statutes')
                self.assertEqual(result['expected_outputs'],self.payload()['expected_outputs'])

    def test_native_payload_does_not_force_claude_model(self):
        with patch.dict(os.environ,{'MEMOFORGE_PLATFORM':'codex'}):
            result=adapt_agent(self.payload(),get_profile(ROOT),ROOT)
            self.assertNotIn('model',result)
