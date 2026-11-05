import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from trader.mt5_bridge import MT5Bridge


class MT5BridgeTests(unittest.TestCase):
    def fake_module(self):
        module=Mock()
        module.initialize.return_value=True
        module.symbol_select.return_value=True
        module.ACCOUNT_TRADE_MODE_DEMO=0
        module.TIMEFRAME_M1=1
        module.account_info.return_value=SimpleNamespace(login=12345678,server='Exness-MT5Trial',
            currency='USD',balance=5000,equity=5002,profit=2,trade_mode=0)
        module.symbol_info_tick.return_value=SimpleNamespace(bid=2650.0,ask=2650.2,time=1700000000)
        module.symbol_info.return_value=SimpleNamespace(digits=2)
        module.copy_rates_from_pos.return_value=[dict(time=1700000000,open=2649,high=2651,low=2648,close=2650,tick_volume=300)]
        return module

    def test_read_only_connection_masks_account(self):
        module=self.fake_module()
        with patch('trader.mt5_bridge.platform.system',return_value='Windows'),patch('trader.mt5_bridge.importlib.import_module',return_value=module):
            bridge=MT5Bridge()
            state=bridge.connect(symbol='XAUUSDm')
            self.assertTrue(state['connected'])
            self.assertEqual(state['account'],'••••5678')
            self.assertEqual(state['candles'][0]['close'],2650)
            self.assertTrue(state['read_only'])
            module.order_send.assert_not_called()
            bridge.disconnect()
            module.shutdown.assert_called_once()

    def test_unknown_broker_symbol_reports_suffix_hint(self):
        module=self.fake_module()
        module.symbol_select.return_value=False
        with patch('trader.mt5_bridge.platform.system',return_value='Windows'),patch('trader.mt5_bridge.importlib.import_module',return_value=module):
            with self.assertRaisesRegex(ValueError,'XAUUSDm'):
                MT5Bridge().connect(symbol='MISSING')
        module.shutdown.assert_called_once()

    def test_dropped_connection_does_not_keep_stale_connected_status(self):
        module=self.fake_module()
        with patch('trader.mt5_bridge.platform.system',return_value='Windows'),patch('trader.mt5_bridge.importlib.import_module',return_value=module):
            bridge=MT5Bridge()
            bridge.connect()
            module.account_info.return_value=None
            state=bridge.snapshot()
            self.assertFalse(state['connected'])
            self.assertTrue(state['error'])


if __name__=='__main__':
    unittest.main()
