"""Prevent destructive Windows shell transcoding in human-facing source text."""
from pathlib import Path
import re
import unittest
class Utf8SourcesTests(unittest.TestCase):
    def test_human_facing_source_has_no_replacement_question_mark_runs(self):
        root=Path(__file__).resolve().parents[1]
        paths=[root/'scripts'/n for n in ('ai_factor_trader.py','ai_brain_trader.py','decision_reporting.py','profit_protection.py','execution_profiles.py')]
        paths += list((root/'okxquant_frontend/src').rglob('*.vue'))
        for path in paths:
            with self.subTest(path=str(path.relative_to(root))):
                text=path.read_bytes().decode('utf-8',errors='strict')
                self.assertNotIn(chr(0xfffd),text)
                self.assertIsNone(re.search(r'[?]{3,}',text))
