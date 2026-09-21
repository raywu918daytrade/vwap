"""Regression checks without broker login or production data."""
import json
import asyncio
import tempfile
import threading
import time
import types
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd

# The Linux-only broker binary is unnecessary for collector unit tests.
sys.modules.setdefault('fubon.fubon_api', types.ModuleType('fubon.fubon_api'))
from fubon import marketdata_ws as ws
from data import chart_cache

TW = timezone(timedelta(hours=8))


def row(sid, minute, close=10):
    return dict(stock_id=sid, date=minute, open=close, high=close, low=close,
                close=close, volume=1)


class ChartCacheTest(unittest.TestCase):
    def test_union_live_wins_refresh_and_mutation_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / 'db/m1_live/2026-09-21.parquet'
            batch = root / 'db/m1/2026_09.parquet'
            live.parent.mkdir(parents=True)
            batch.parent.mkdir(parents=True)
            pd.DataFrame([row('2330', '2026-09-21 09:00:00', 1),
                          row('2330', '2026-09-21 09:01:00', 2),
                          row('2330', '2026-09-18 09:00:00', 99)]).to_parquet(batch)
            pd.DataFrame([row('2330', '2026-09-21 09:01:00', 3)]).to_parquet(live)
            a = chart_cache.current_session(root, '2330', '2026-09-21')
            self.assertEqual(a.close.tolist(), [1, 3])
            a.loc[0, 'close'] = 999
            self.assertEqual(chart_cache.current_session(root, '2330', '2026-09-21').close.tolist(), [1, 3])
            pd.DataFrame([row('2330', '2026-09-21 09:01:00', 4),
                          row('2330', '2026-09-21 09:02:00', 5)]).to_parquet(live)
            self.assertEqual(chart_cache.current_session(root, '2330', '2026-09-21').close.tolist(), [1, 4, 5])
            self.assertIsNone(chart_cache.current_session(root, '9999', '2026-09-21'))

    def test_timestamp_parquet_filters_to_one_trading_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            live = root / 'db/m1_live/2026-09-21.parquet'
            live.parent.mkdir(parents=True)
            data = pd.DataFrame([row('2330', '2026-09-20 09:00:00', 1),
                                 row('2330', '2026-09-21 09:00:00', 2),
                                 row('2330', '2026-09-22 00:00:00', 3)])
            data['date'] = pd.to_datetime(data['date'])
            data.to_parquet(live)
            self.assertEqual(chart_cache.current_session(root, '2330', '2026-09-21').close.tolist(), [2])

    def test_concurrent_read_fills_once(self):
        from concurrent.futures import ThreadPoolExecutor
        calls = []
        def load():
            calls.append(1)
            time.sleep(.02)
            return pd.DataFrame({'close': [1]})
        key = ('test-flight', time.monotonic())
        with ThreadPoolExecutor(2) as pool:
            frames = list(pool.map(lambda _: chart_cache.cached_frame(key, load), range(2)))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(frames), 2)


class CollectorTest(unittest.TestCase):
    def test_timestamp_and_pong_diagnostics(self):
        raw = {'event': 'data', 'data': {'symbol': '2330', 'date': '2026-09-21T01:01:00Z',
                                        'open': 1, 'high': 2, 'low': 1, 'close': 2, 'volume': 3}}
        self.assertEqual(ws._parse_candle(raw)['date'], '2026-09-21 09:01:00')
        collector = ws.FubonM1Collector()
        collector._make_handler(1)(json.dumps({'event': 'pong', 'data': {}}))
        collector._clients = [types.SimpleNamespace(missed_pongs=3)]
        collector._mark_connection_failed(1, 'disconnected: None None')
        self.assertIn('missed_pongs=3', collector._connection_failure_reason)
        self.assertIn('pong_age_s', collector._connection_failure_reason)

    def test_backfill_only_missing_and_never_overwrites_live(self):
        class FixedDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 21, 9, 3, tzinfo=TW)
        existing = pd.DataFrame([row('2330', '2026-09-21 09:00:00'),
                                 row('2330', '2026-09-21 09:02:00'),
                                 *[row('0050', f'2026-09-21 09:0{i}:00') for i in range(3)]])
        collector = ws.FubonM1Collector(backfill_done=threading.Event())
        collector._buffer[('2330', '2026-09-21 09:01:00')] = row('2330', '2026-09-21 09:01:00', 99)
        with patch.object(ws, 'datetime', FixedDate), patch.object(ws, 'load_m1_live', return_value=existing), \
             patch.object(ws.trade_api, 'intraday_candles', create=True, return_value=[]) as api, \
             patch.object(ws, '_parse_rest_bars', return_value=pd.DataFrame([row('2330', f'2026-09-21 09:0{i}:00', 5) for i in range(3)])), \
             patch.object(collector, '_flush'), patch.object(ws, '_log_sys'):
            collector._backfill_m1_live(['2330', '0050'], '2026-09-21')
        api.assert_called_once_with(None, '2330')
        self.assertEqual(len(collector._buffer), 1)
        self.assertEqual(collector._buffer[('2330', '2026-09-21 09:01:00')]['close'], 99)
        self.assertTrue(collector._backfill_done.is_set())

    def test_backfill_does_not_replace_a_websocket_bar_already_flushed(self):
        collector = ws.FubonM1Collector()
        collector._backfill_tracking = True
        collector._make_handler(1)(json.dumps({'event': 'data', 'data': {
            'symbol': '2330', 'date': '2026-09-21T09:01:00+08:00',
            'open': 99, 'high': 99, 'low': 99, 'close': 99, 'volume': 1}}))
        collector._buffer.clear()  # Simulate the periodic flush during REST I/O.
        class FixedDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 21, 9, 2, tzinfo=TW)
        with patch.object(ws, 'datetime', FixedDate), patch.object(ws, 'load_m1_live', return_value=pd.DataFrame()), \
             patch.object(ws.trade_api, 'intraday_candles', create=True, return_value=[]), \
             patch.object(ws, '_parse_rest_bars', return_value=pd.DataFrame([row('2330', '2026-09-21 09:01:00', 5)])), \
             patch.object(collector, '_flush'), patch.object(ws, '_log_sys'):
            collector._backfill_missing(['2330'], '2026-09-21')
        self.assertEqual(collector._buffer, {})

    def test_stopped_generation_does_not_write_or_mark_ready(self):
        class FixedDate(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 9, 21, 9, 3, tzinfo=TW)
        collector = ws.FubonM1Collector(backfill_done=threading.Event())
        def stop_during_request(*args):
            collector._stop = True
            return []
        with patch.object(ws, 'datetime', FixedDate), patch.object(ws, 'load_m1_live', return_value=pd.DataFrame()), \
             patch.object(ws.trade_api, 'intraday_candles', create=True, side_effect=stop_during_request), \
             patch.object(ws, '_log_sys'):
            collector._backfill_m1_live(['2330'], '2026-09-21')
        self.assertFalse(collector._backfill_done.is_set())
        self.assertEqual(collector._buffer, {})

    def test_old_backfill_blocks_new_until_finished(self):
        old = ws.FubonM1Collector()
        new = ws.FubonM1Collector()
        entered = threading.Event()
        release = threading.Event()
        def old_work(*args):
            entered.set()
            release.wait(2)
        with patch.object(old, '_backfill_missing', side_effect=old_work), \
             patch.object(new, '_backfill_missing') as new_work:
            first = threading.Thread(target=old._backfill_m1_live, args=([], '2026-09-21'))
            second = threading.Thread(target=new._backfill_m1_live, args=([], '2026-09-21'))
            first.start()
            self.assertTrue(entered.wait(1))
            second.start()
            time.sleep(.05)
            new_work.assert_not_called()
            release.set()
            first.join(2)
            second.join(2)
            self.assertFalse(first.is_alive() or second.is_alive())
            new_work.assert_called_once()


class RequestConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_two_requests_run_together_third_waits(self):
        import api
        import httpx
        active = 0
        peak = 0
        async def endpoint():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.05)
            active -= 1
            return {"ok": True}
        api.app.add_api_route('/api/pattern/test-concurrency', endpoint)
        try:
            with patch.object(api, '_LOW_MEMORY_RUNTIME', True), \
                 patch.object(api, '_data_ready', True), \
                 patch.object(api, '_heavy_request_lock', asyncio.Semaphore(2)), \
                 patch.object(api, '_trim_memory_if_due'), patch.object(api, 'append_system_log'):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url='http://test') as client:
                    responses = await asyncio.gather(*[client.get('/api/pattern/test-concurrency') for _ in range(3)])
            self.assertEqual(peak, 2)
            self.assertTrue(all(r.status_code == 200 for r in responses))
            self.assertTrue(all('queue;dur=' in r.headers['server-timing'] for r in responses))
        finally:
            api.app.router.routes.pop()


if __name__ == '__main__':
    unittest.main()
