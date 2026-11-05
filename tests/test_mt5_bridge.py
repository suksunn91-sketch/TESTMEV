import unittest
from unittest.mock import patch
from fixtures import fake_mt5
from trader.mt5_bridge import MT5Bridge


class MT5BridgeTests(unittest.TestCase):
    def setUp(self):
        self.module=fake_mt5()
        self.platform=patch("trader.mt5_bridge.platform.system",return_value="Windows")
        self.loader=patch("trader.mt5_bridge.importlib.import_module",return_value=self.module)
        self.platform.start();self.loader.start()
        self.addCleanup(self.platform.stop);self.addCleanup(self.loader.stop)
        self.bridge=MT5Bridge()

    def test_exact_broker_quotes_and_metadata(self):
        state=self.bridge.connect(symbol="XAUUSDm")
        self.assertTrue(state["connected"])
        self.assertEqual(state["account"],"••••5678")
        self.assertEqual(state["ask"],2650.3)
        self.assertEqual(state["contract"],100)
        self.assertEqual(len(state["candles"]),100)
        self.module.order_send.assert_not_called()

    def test_timeframe_maps_to_real_mt5_request(self):
        self.bridge.connect(timeframe="M5")
        self.module.copy_rates_from_pos.assert_called_with("XAUUSDm",5,0,300)
        self.bridge.timeframe="M15"
        self.bridge.snapshot()
        self.module.copy_rates_from_pos.assert_called_with("XAUUSDm",15,0,300)

    def test_history_excludes_forming_bar(self):
        self.bridge.connect()
        bars,state=self.bridge.history()
        self.module.copy_rates_from_pos.assert_called_with("XAUUSDm",1,1,800)
        self.assertEqual(len(bars),99)

    def test_unknown_symbol(self):
        self.module.symbol_select.return_value=False
        with self.assertRaisesRegex(ValueError,"XAUUSDm"): self.bridge.connect(symbol="MISSING")
        self.assertFalse(self.bridge.connected)

    def test_terminal_disconnected_even_when_cached_account_exists(self):
        self.bridge.connect()
        self.module.terminal_info.return_value.connected=False
        self.assertFalse(self.bridge.snapshot()["connected"])

    def test_zero_quote_does_not_substitute_price(self):
        self.module.symbol_info_tick.return_value.bid=0
        result=self.bridge.connect()
        self.assertFalse(result["connected"])
        self.assertNotIn("bid",result)

    def test_missing_candles_do_not_get_generated(self):
        self.module.copy_rates_from_pos.side_effect=None
        self.module.copy_rates_from_pos.return_value=None
        state=self.bridge.connect()
        self.assertEqual(state["candles"],[])
        self.assertTrue(state["error"])

    def test_disconnect_clears_terminal_handle(self):
        self.bridge.connect()
        self.bridge.disconnect()
        self.module.shutdown.assert_called_once()
        self.assertIsNone(self.bridge.mt5)
        self.assertFalse(self.bridge.snapshot()["connected"])

    def test_bad_history_is_reported(self):
        self.module.copy_rates_from_pos.side_effect=None
        self.module.copy_rates_from_pos.return_value=[dict(time=1,open=10,high=1,low=0,close=10,tick_volume=1,spread=0)]
        with self.assertRaises(ValueError): self.bridge.connect()


if __name__=="__main__":
    unittest.main()
