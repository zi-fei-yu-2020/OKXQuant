"""No cross-thread/asynchronous frame contamination; explicit worker inheritance."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import threading
import unittest
from unittest.mock import patch
from scripts import public_market as market


class SignalFrameTests(unittest.TestCase):
    def setUp(self):
        token = market._SIGNAL_AS_OF.set(None)
        self.addCleanup(market._SIGNAL_AS_OF.reset, token)

    def test_independent_collectors_keep_their_frames(self):
        barrier = threading.Barrier(2)
        def collect(value):
            market.begin_signal_frame(value); barrier.wait(timeout=3)
            return market.signal_as_of()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(collect, 120); b = pool.submit(collect, 180)
            self.assertEqual((a.result(), b.result()), (120, 180))
        self.assertIsNone(market._SIGNAL_AS_OF.get())

    def test_workers_inherit_frame_captured_before_submit(self):
        market.begin_signal_frame(120)
        work = market.bind_signal_frame(lambda _: market.signal_as_of())
        market.begin_signal_frame(180)
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(list(pool.map(work, range(20))), [120] * 20)
        self.assertEqual(market.signal_as_of(), 180)

    def test_reused_worker_context_is_restored_after_exception(self):
        def fail():
            self.assertEqual(market.signal_as_of(), 120)
            market.begin_signal_frame(240)
            raise RuntimeError("test")
        market.begin_signal_frame(120); work = market.bind_signal_frame(fail)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(market.begin_signal_frame, 60).result()
            with self.assertRaises(RuntimeError): pool.submit(work).result()
            self.assertEqual(pool.submit(market.signal_as_of).result(), 60)

    def test_independent_async_tasks_do_not_share_frame(self):
        async def collect(value):
            market.begin_signal_frame(value); await asyncio.sleep(0)
            return market.signal_as_of()
        async def run(): return await asyncio.gather(collect(120), collect(180))
        self.assertEqual(asyncio.run(run()), [120, 180])

    def test_deadline_context_is_inherited_alongside_frame(self):
        market.begin_signal_frame(120)
        work = market.run_with_deadline(999, market.bind_signal_frame,
            lambda: (market.signal_as_of(), market._DEADLINE.get()))
        with ThreadPoolExecutor(max_workers=1) as pool:
            self.assertEqual(pool.submit(work).result(), (120, 999))
        self.assertIsNone(market._DEADLINE.get())

    def test_no_frame_uses_one_clock_read_before_http_and_filter(self):
        row = ["60000", "10", "12", "9", "11", "2", "1", "1", "1"]
        with patch.object(market.time, "time", side_effect=[120, 181]), patch.object(market, "get_json", return_value={"code":"0", "data":[row]}):
            result = market.signal_json(market.BASE_URL + "/api/v5/market/candles?bar=1m&limit=1")
        self.assertEqual(result["as_of_ms"], 120000)
        self.assertEqual(result["data"], [row])

    def test_intrabar_and_future_closed_rows_cannot_enter_frame(self):
        def row(ts, confirm): return [str(ts), "10", "12", "9", "11", "2", "1", "1", confirm]
        market.begin_signal_frame(180)
        payload={"code":"0", "data":[row(180000,"1"),row(120000,"0"),row(60000,"1")]}
        with patch.object(market, "get_json", return_value=payload):
            result=market.signal_json(market.BASE_URL + "/api/v5/market/candles?bar=1m&limit=3")
        self.assertEqual([r[0] for r in result["data"]], ["60000"])
        self.assertEqual(result["candle_contract"], "closed-v1")

    def test_invalid_timestamp_does_not_replace_good_frame(self):
        market.begin_signal_frame(120)
        for value in (float("nan"),float("inf"),-1,0):
            with self.assertRaises(ValueError): market.begin_signal_frame(value)
        self.assertEqual(market.signal_as_of(), 120)


class FrozenEnvironmentTests(unittest.TestCase):
    def test_concurrent_cycles_have_independent_frozen_bindings(self):
        from scripts import okx_runtime
        barrier=threading.Barrier(2)
        def freeze(mode):
            try:
                okx_runtime.freeze_environment({"OKXQUANT_OKX_ENV":mode,"OKX_API_KEY":mode,"OKX_SECRET_KEY":"S","OKX_PASSPHRASE":"P"})
                barrier.wait(timeout=3)
                return market._selected().mode
            finally:okx_runtime.unfreeze_environment()
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(freeze,"demo");b=pool.submit(freeze,"live")
            self.assertEqual((a.result(),b.result()),("demo","live"))

    def test_worker_inherits_frozen_credentials_and_frame_together(self):
        from scripts import okx_runtime
        token=market._SIGNAL_AS_OF.set(None)
        try:
            frozen=okx_runtime.freeze_environment({"OKXQUANT_OKX_ENV":"live","OKX_API_KEY":"L","OKX_SECRET_KEY":"S","OKX_PASSPHRASE":"P"})
            market.begin_signal_frame(180)
            work=market.bind_signal_frame(lambda:(market._selected(),market.signal_as_of()))
            okx_runtime.unfreeze_environment()
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(work).result(),(frozen,180))
        finally:
            okx_runtime.unfreeze_environment();market._SIGNAL_AS_OF.reset(token)
