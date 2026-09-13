"""File freshness does not mean upstream data or a trader cycle succeeded."""
import ast
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from okxquant_backend import app

class DataHealthSemanticsTests(unittest.TestCase):
    def test_freshly_written_unconfigured_news_is_not_healthy(self):
        with tempfile.TemporaryDirectory() as td, patch.object(app,'DATA_DIR',Path(td)):
            path=Path(td)/'news_sentiment.json'
            for state in ('unconfigured','partial','unavailable'):
                path.write_text(json.dumps({'connection_status':state,'last_success_at':time.time()}))
                result=app.file_health('news_sentiment.json',600)
                self.assertFalse(result['fresh'])
                self.assertEqual(result['data_status'],state)
            path.write_text(json.dumps({'connection_status':'fresh','last_success_at':time.time()}))
            self.assertTrue(app.file_health('news_sentiment.json',600)['fresh'])
            path.write_text(json.dumps({'connection_status':'fresh','last_success_at':None}))
            self.assertFalse(app.file_health('news_sentiment.json',600)['fresh'])
            path.write_text('{')
            self.assertFalse(app.file_health('news_sentiment.json',600)['fresh'])

    def test_trader_cli_marks_aborted_cycle_failed_without_retry(self):
        path=Path(__file__).resolve().parents[1]/'scripts/ai_factor_trader.py'
        module=ast.parse(path.read_text(encoding='utf-8'))
        guard=module.body[-1]
        for result,expected in ((None,1),({},None),({'ok':True},None)):
            calls=[]
            def cycle():calls.append(True);return result
            namespace={'__name__':'__main__','execute_portfolio':cycle}
            snippet=compile(ast.Module(body=[guard],type_ignores=[]),str(path),'exec')
            if expected is None:exec(snippet,namespace)
            else:
                with self.assertRaises(SystemExit) as exc:exec(snippet,namespace)
                self.assertEqual(exc.exception.code,expected)
            self.assertEqual(len(calls),1)
