import copy
import unittest
from unittest.mock import patch
from fixtures import market
from trader.engine import PaperEngine, run_backtest, validated_settings


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.now=1800000000.
        self.engine=PaperEngine(clock=lambda:self.now)
        self.feed=market(self.now)
        self.engine.apply_market(self.feed)

    def test_startup_has_no_fabricated_prices(self):
        state=PaperEngine().snapshot()
        self.assertEqual(state["candles"],[])
        self.assertIsNone(state["bid"])
        self.assertEqual(state["market"]["status"],"disconnected")
        with self.assertRaises(ValueError):
            PaperEngine().order("buy")

    def test_bid_ask_and_bars_are_exact_broker_values(self):
        state=self.engine.snapshot()
        self.assertEqual(state["bid"],2650.)
        self.assertEqual(state["ask"],2650.3)
        self.assertEqual(state["candles"],self.feed["candles"])

    def test_same_bar_updates_without_duplicating_or_generating_bars(self):
        updated=copy.deepcopy(self.feed)
        updated["candles"][-1]["close"]+=.25
        updated["tick_time"]+=1
        self.engine.apply_market(updated)
        self.assertEqual(len(self.engine.candles),100)
        self.assertEqual(self.engine.candles[-1],updated["candles"][-1])

    def test_spread_and_commission(self):
        p=self.engine.order("buy")
        self.assertEqual(p["entry"],2650.3)
        self.assertEqual(p["symbol"],"XAUUSDm")
        self.assertAlmostEqual(self.engine.close(p["id"])["net"],-.37)

    def test_short_uses_real_ask_to_close(self):
        p=self.engine.order("sell")
        self.engine.apply_market({**self.feed,"bid":2647.,"ask":2647.5})
        self.assertAlmostEqual(self.engine.pnl(p),2.5)
        self.assertAlmostEqual(self.engine.close(p["id"])["net"],2.43)

    def test_close_is_not_repeatable(self):
        p=self.engine.order("buy")
        self.engine.close(p["id"])
        balance=self.engine.balance
        with self.assertRaises(ValueError):
            self.engine.close(p["id"])
        self.assertEqual(balance,self.engine.balance)

    def test_stop_loss_executes_only_when_fresh_real_quote_arrives(self):
        p=self.engine.order("buy")
        self.engine.apply_market({**self.feed,"tick_time":self.now-20,"bid":2630.,"ask":2630.3})
        self.assertEqual(len(self.engine.positions),1)
        self.engine.apply_market({**self.feed,"bid":2630.,"ask":2630.3})
        self.assertEqual(self.engine.history[0]["reason"],"stop loss")
        self.assertEqual(self.engine.history[0]["exit"],2630.)

    def test_short_take_profit(self):
        self.engine.order("sell")
        self.engine.apply_market({**self.feed,"bid":2630.,"ask":2630.3})
        self.assertEqual(self.engine.history[0]["reason"],"take profit")

    def test_maximum_positions_and_insufficient_margin(self):
        self.engine.configure({"max_positions":1})
        self.engine.order("buy")
        with self.assertRaises(ValueError): self.engine.order("buy")
        self.engine.close_all()
        self.engine.configure({"volume":5})
        with self.assertRaises(ValueError): self.engine.order("buy")

    def test_symbol_volume_rules_from_broker(self):
        self.engine.apply_market({**self.feed,"volume_min":.1,"volume_step":.1})
        with self.assertRaisesRegex(ValueError,"Lot"): self.engine.order("buy")

    def test_stale_tick_pauses_bot_and_rejects_orders(self):
        self.engine.running=True
        self.engine.apply_market({**self.feed,"tick_time":self.now-16})
        self.assertFalse(self.engine.running)
        self.assertEqual(self.engine.market_status()["status"],"stale")
        with self.assertRaises(ValueError): self.engine.order("buy")

    def test_poll_stall_expires_even_if_no_new_feed_response(self):
        self.engine.running=True
        self.now+=4
        self.assertEqual(self.engine.snapshot()["market"]["status"],"stale")
        self.assertFalse(self.engine.running)

    def test_disconnect_freezes_last_bars_and_never_falls_back(self):
        self.engine.apply_market({"connected":False,"error":"offline"})
        self.assertEqual(self.engine.candles,self.feed["candles"])
        self.assertEqual(self.engine.quote("buy"),2650.3)
        self.assertEqual(self.engine.market_status()["status"],"disconnected")
        with self.assertRaises(ValueError): self.engine.order("buy")

    def test_no_history_is_waiting_and_no_quote_is_invented(self):
        self.engine.apply_market({**self.feed,"candles":[]})
        self.assertEqual(self.engine.candles,[])
        self.assertEqual(self.engine.market_status()["status"],"waiting")
        with self.assertRaises(ValueError): self.engine.order("buy")

    def test_no_forming_bar_or_repeated_bar_signals(self):
        self.engine.running=True
        with patch("trader.engine.ema",return_value=[0]*100) as ema:
            self.engine.apply_market(self.feed)
            self.engine.apply_market(self.feed)
            ema.assert_not_called()

    def test_closed_bar_signal_processed_once(self):
        self.engine.running=True
        updated=copy.deepcopy(self.feed)
        final=dict(updated["candles"][-1],time=updated["candles"][-1]["time"]+60)
        updated["candles"].append(final)
        with patch("trader.engine.ema",side_effect=[[0,2],[1,1]]) as calc:
            self.engine.apply_market(updated)
            self.engine.apply_market(updated)
            self.assertEqual(calc.call_count,2)
        self.assertEqual(len(self.engine.positions),1)

    def test_cannot_change_symbol_with_open_position(self):
        self.engine.order("buy")
        with self.assertRaises(ValueError): self.engine.configure({"symbol":"EURUSDm"})

    def test_reject_invalid_settings(self):
        for bad in [{"volume":float("nan")},{"volume":float("inf")},{"volume":.015},
                    {"max_positions":1.5},{"fast":22,"slow":21},{"symbol":"../bad"},
                    {"timeframe":"M7"},{"unknown":1}]:
            with self.subTest(bad=bad),self.assertRaises(ValueError): validated_settings(bad)

    def test_backtest_requires_actual_input_bars(self):
        with self.assertRaises(ValueError): run_backtest({},[],self.feed)
        result=run_backtest({},self.feed["candles"],self.feed)
        self.assertEqual(result["data_source"],"mt5")
        self.assertEqual(result["bars"],100)
        self.assertGreater(result["trades"],0)

    def test_backtest_does_not_look_at_unseen_current_close(self):
        bars=copy.deepcopy(self.feed["candles"])
        first=run_backtest({},bars,self.feed)
        bars[-1]["close"]+=1000
        bars[-1]["high"]+=1000
        second=run_backtest({},bars,self.feed)
        self.assertEqual(first["net"],second["net"])

    def test_non_usd_symbol_still_charts_but_does_not_misprice_orders(self):
        self.engine.apply_market({**self.feed,"currency_profit":"JPY"})
        self.assertEqual(len(self.engine.candles),100)
        with self.assertRaises(ValueError): self.engine.order("buy")


if __name__=="__main__":
    unittest.main()
