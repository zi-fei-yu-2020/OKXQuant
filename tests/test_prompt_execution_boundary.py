"""Read-only prompt imports must not require, or weaken, POSIX execution locks."""
from __future__ import annotations
import runpy
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))


class PromptExecutionBoundaryTests(unittest.TestCase):
    def test_prompt_definitions_import_without_fcntl(self):
        for filename, constant in [
            ('ai_brain_trader.py', 'SYSTEM_PROMPT'),
            ('self_improvement_engine.py', 'EVOLUTION_SYSTEM_PROMPT'),
        ]:
            with self.subTest(script=filename), patch.dict(sys.modules, {'fcntl': None}):
                module = runpy.run_path(str(SCRIPTS / filename), run_name='readonly_prompt_probe')
                self.assertIsInstance(module[constant], str)
                self.assertTrue(module[constant].strip())

    def test_missing_posix_lock_refuses_execution_before_action_or_file_access(self):
        from scripts.ai_brain_trader import single_brain_cycle
        from scripts.self_improvement_engine import single_evolution_cycle
        for decorator in (single_brain_cycle, single_evolution_cycle):
            with self.subTest(decorator=decorator.__name__):
                action = Mock()
                wrapped = decorator(action)
                with patch.dict(sys.modules, {'fcntl': None}), patch('builtins.open') as opened:
                    with self.assertRaises(ModuleNotFoundError):
                        wrapped()
                    action.assert_not_called()
                    opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
