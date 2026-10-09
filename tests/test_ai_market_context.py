import copy,unittest
from scripts.market_context import select_reports,context,usage_receipt,related

class MarketContextTests(unittest.TestCase):
    def item(self,id,title,summary='',at=1000,coins=None):
        return {'id':id,'title':title,'summary':summary,'source_at':at,'coins':coins or [],'importance':'normal','url':''}
    def test_recent_macro_report_is_not_crowded_out_by_coin_headlines(self):
        items=[self.item(str(i),'BTC市场报道'+str(i),at=1000+i,coins=['BTC']) for i in range(9)]
        items.append(self.item('macro','美股纳斯达克指数上涨，美元指数回落',at=995))
        selected,meta=select_reports(items,['BTC'],1100)
        self.assertEqual(len(selected),6);self.assertIn('macro',[r['id'] for r in selected])
        self.assertGreaterEqual(meta['categories']['macro_report'],1)
    def test_related_reports_remain_visible_and_are_not_independent_confirmations(self):
        text='Robinhood announced plans for tokenized actively managed ETF assets in partnership with a large asset manager for European customers'
        a=self.item('a','Robinhood explores tokenized ETF assets',text)
        b=self.item('b','Robinhood explores tokenized ETF assets with an asset manager',text,at=999)
        selected,_=select_reports([a,b],['BTC'],1100)
        self.assertEqual(len(selected),1);self.assertEqual(selected[0]['related_report_count'],1)
        self.assertEqual(len(selected[0]['related_reports']),1)
        self.assertIn('not independent',selected[0]['grouping_note'])
    def test_conflicting_numbers_or_negations_are_never_dropped_as_duplicates(self):
        a=self.item('a','CPI报告','CPI actual 3.0 percent compared with the same earlier report and reference month')
        b=self.item('b','CPI报告','CPI actual 4.0 percent compared with the same earlier report and reference month')
        self.assertFalse(related(a,b));self.assertEqual(len(select_reports([a,b],['BTC'],1100)[0]),2)
        c=self.item('c','平台消息','平台否认暂停提现，服务正常')
        d=self.item('d','平台消息','平台暂停提现，服务异常')
        self.assertEqual(len(select_reports([c,d],['BTC'],1100)[0]),2)
    def test_old_macro_story_does_not_force_out_current_news(self):
        items=[self.item(str(i),'BTC新事件'+str(i),at=1000+i,coins=['BTC']) for i in range(8)]
        items.append(self.item('old','美股昨日上涨',at=-8000))
        self.assertNotIn('old',[x['id'] for x in select_reports(items,['BTC'],1100)[0]])
    def test_context_never_invents_stock_quotes_from_headlines(self):
        s={'items':[self.item('x','标普上涨1%')],'connection_status':'fresh','sentiment_fresh':True}
        out=context(s)
        self.assertEqual(out['cross_asset_quotes'],{'status':'not_connected','values':{}})
        self.assertEqual(out['economic_release_values']['status'],'not_connected')
    def test_receipt_counts_validated_references_not_attention_weights(self):
        snap={'items':[self.item('x','新闻标题')],'digest':'abc'}
        ref='/news/articles/0/title';catalog={'BTC':{ref:{'value':'新闻标题','group':'news'}}}
        decisions={'BTC':{'contract_valid':True,'supporting_evidence':[{'ref':ref,'value':'新闻标题'}]}}
        r=usage_receipt(snap,decisions,catalog)
        self.assertEqual(r['cited_article_count'],1);self.assertEqual(r['status'],'cited')
        self.assertIn('not_internal_weights',r['semantics'])
        decisions['BTC']['supporting_evidence'][0]['value']='invented'
        r=usage_receipt(snap,decisions,catalog)
        self.assertEqual(r['status'],'provided_without_structured_citation')
    def test_invalid_decision_or_raw_proposal_cannot_claim_citation(self):
        ref='/news/articles/0/title';snap={'items':[self.item('x','title')]}
        catalog={'BTC':{ref:{'value':'title'}}}
        d={'BTC':{'contract_valid':False,'supporting_evidence':[{'ref':ref,'value':'title'}]}}
        self.assertEqual(usage_receipt(snap,d,catalog)['cited_article_count'],0)
        d={'BTC':{'contract_valid':True,'raw_proposal':{'supporting_evidence':[{'ref':ref,'value':'title'}]}}}
        self.assertEqual(usage_receipt(snap,d,catalog)['cited_article_count'],0)
    def test_missing_article_identifier_does_not_crash_valid_price_decision(self):
        ref='/news/articles/0/title';snap={'items':[{'title':'title'}]}
        d={'BTC':{'contract_valid':True,'supporting_evidence':[{'ref':ref,'value':'title'}]}}
        self.assertEqual(usage_receipt(snap,d,{'BTC':{ref:{'value':'title'}}})['cited_article_count'],0)

    def test_arbitrary_extra_response_field_does_not_claim_usage(self):
        ref='/news/articles/0/title';snap={'items':[self.item('x','title')]}
        d={'BTC':{'contract_valid':True,'unreviewed_extra':{'ref':ref,'value':'title'}}}
        self.assertEqual(usage_receipt(snap,d,{'BTC':{ref:{'value':'title'}}})['cited_article_count'],0)
