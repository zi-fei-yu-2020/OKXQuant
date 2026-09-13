from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import public_market as market
from okxquant_backend import account_connections as accounts, connection_transport as transport


def connection(identity='a'*32, secret='UNIT_SECRET'):
    return {'id':identity,'generation':1,'site':'global','auth_type':'api_key',
            'credentials':{'api_key':'UNIT_KEY','secret_key':secret,'passphrase':'UNIT_PASSPHRASE'},
            'capabilities':{'live':{'account_uid':'unit-identity'}}}


class SmartMoneyNewsTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.cache=Path(temp.name)
        self.state={'managed':True,'active_mode':'demo'}
        self.c=connection()
        self.rows=[{'ccy':'BTC','longShortRatio':{'weightedLongRatio':'.7'},'notional':{'netNotionalUsdt':'100'}}]
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(market,'CACHE_DIR',self.cache))
        self.real_request=transport.request
        self.load=self.stack.enter_context(patch.object(accounts,'load',return_value=self.state))
        self.bound=self.stack.enter_context(patch.object(accounts,'news_connection',return_value=self.c))
        self.http=self.stack.enter_context(patch.object(transport,'request',return_value=self.rows))
        self.cli=self.stack.enter_context(patch.object(market.subprocess,'run',side_effect=AssertionError('Managed reads must not start Node CLI')))
        self.selected=self.stack.enter_context(patch.object(market,'_selected',side_effect=AssertionError('Do not borrow the trading environment')))

    def test_demo_trading_uses_only_the_bound_regular_information_connection(self):
        result=market.smart_money_overview(['ETH','BTC','BTC'])
        self.assertEqual(result,self.rows)
        args=self.http.call_args.args
        self.assertEqual(args[:5],(self.c,'news','live','GET','/api/v5/journal/smartmoney/overview'))
        self.assertEqual(args[5],{'instCcyList':'BTC,ETH'})
        self.assertEqual(self.state['active_mode'],'demo')
        self.cli.assert_not_called(); self.selected.assert_not_called()

    def test_cache_is_bound_to_news_identity_and_rotated_credentials(self):
        market.smart_money_overview(['BTC']);market.smart_money_overview(['BTC'])
        self.assertEqual(self.http.call_count,1)
        self.bound.return_value=connection(secret='ROTATED_UNIT_SECRET')
        market.smart_money_overview(['BTC']);self.assertEqual(self.http.call_count,2)
        self.bound.return_value=connection('b'*32)
        market.smart_money_overview(['BTC']);self.assertEqual(self.http.call_count,3)
        content=''.join(p.read_text() for p in self.cache.glob('*.json'))
        for secret in ('UNIT_KEY','UNIT_SECRET','UNIT_PASSPHRASE','ROTATED_UNIT_SECRET'):self.assertNotIn(secret,content)

    def test_failed_unbound_source_does_not_fall_back_to_demo_or_another_profile(self):
        self.bound.side_effect=accounts.AccountChangeError('news unbound')
        with self.assertRaises(accounts.AccountChangeError):market.smart_money_overview(['BTC'])
        self.http.assert_not_called();self.cli.assert_not_called();self.selected.assert_not_called()

    def test_empty_error_and_duplicate_responses_are_not_cached_as_observations(self):
        for rows in ([],[None],[{'ccy':'OTHER'}],[self.rows[0],self.rows[0]],{'data':[]}):
            with self.subTest(rows=rows):
                self.http.return_value=rows
                with self.assertRaises(market.MarketDataError):market.smart_money_overview(['BTC'])
                self.assertEqual(list(self.cache.glob('*.json')),[])
        self.http.side_effect=transport.ConnectionError('http_503')
        with self.assertRaises(transport.ConnectionError):market.smart_money_overview(['BTC'])
        self.cli.assert_not_called()

    def test_rebinding_during_request_cannot_publish_the_old_connection_result(self):
        self.bound.side_effect=[self.c,connection('b'*32)]
        with self.assertRaisesRegex(market.MarketDataError,'changed'):market.smart_money_overview(['BTC'])

    def test_cached_result_cannot_be_mutated_by_another_consumer(self):
        result=market.smart_money_overview(['BTC']);result[0]['notional']['netNotionalUsdt']='9999'
        self.assertEqual(market.smart_money_overview(['BTC'])[0]['notional']['netNotionalUsdt'],'100')

    def test_new_information_endpoint_remains_read_only_and_regular_market_only(self):
        path='/api/v5/journal/smartmoney/overview'
        self.assertIn(path,transport.NEWS_PATHS)
        with patch.object(transport,'credential_headers',side_effect=AssertionError('Do not prepare credentials for prohibited calls')):
            for purpose,mode,method in [('news','demo','GET'),('news','live','POST'),('trade','demo','GET'),('probe','live','POST')]:
                with self.assertRaises(transport.ConnectionError):self.real_request(self.c,purpose,mode,method,path,{})
        with patch.object(transport,'credential_headers',return_value={}),patch.object(transport,'_http',return_value=self.rows) as read:
            result=self.real_request(self.c,'news','live','GET',path,{'instCcyList':'BTC'})
            self.assertEqual(result,self.rows)
            self.assertEqual(read.call_args.args[:2],('GET',path))
