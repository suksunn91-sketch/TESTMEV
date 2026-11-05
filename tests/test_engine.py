import math
import unittest

from trader.engine import PaperEngine, run_backtest, validated_settings


class EngineTests(unittest.TestCase):
    def test_spread_and_commission_are_charged(self):
        engine = PaperEngine()
        entry = engine.order('buy')
        self.assertAlmostEqual(engine.pnl(entry), -0.30)
        trade = engine.close(entry['id'])
        self.assertAlmostEqual(trade['net'], -0.37)
        self.assertAlmostEqual(engine.balance, 4999.63)

    def test_short_profit_and_accounting(self):
        engine = PaperEngine()
        entry = engine.order('sell')
        engine.candles[-1]['close'] -= 3
        self.assertAlmostEqual(engine.equity(), 5002.7)
        engine.close(entry['id'])
        self.assertAlmostEqual(engine.balance, 5002.63)
        self.assertEqual(engine.used_margin(), 0)

    def test_close_is_not_repeatable(self):
        engine = PaperEngine()
        position = engine.order('buy')
        engine.close(position['id'])
        before = engine.balance
        with self.assertRaises(ValueError):
            engine.close(position['id'])
        self.assertEqual(before, engine.balance)

    def test_stop_loss_fills_at_gap_quote(self):
        engine = PaperEngine()
        position = engine.order('buy')
        close = position['sl'] - 2
        engine.tick({'time': engine.candles[-1]['time']+60, 'open': close+10,
                     'high': close+10, 'low': close, 'close': close, 'volume': 100})
        self.assertFalse(engine.positions)
        trade = engine.history[0]
        self.assertEqual(trade['reason'], 'stop loss')
        self.assertAlmostEqual(trade['exit'], close - .15)

    def test_short_take_profit(self):
        engine = PaperEngine()
        position = engine.order('sell')
        close = position['tp'] - 1
        engine.tick({'time':engine.candles[-1]['time']+60,'open':close,'high':close,
                     'low':close,'close':close,'volume':100})
        self.assertEqual(engine.history[0]['reason'], 'take profit')
        self.assertGreater(engine.balance, 5000)

    def test_maximum_positions(self):
        engine = PaperEngine({'max_positions':1})
        engine.order('buy')
        with self.assertRaises(ValueError):
            engine.order('sell')

    def test_insufficient_margin(self):
        engine = PaperEngine({'volume':5})
        with self.assertRaises(ValueError):
            engine.order('buy')

    def test_reject_invalid_settings(self):
        for bad in [{'volume':float('nan')},{'volume':float('inf')},{'volume':0},
                    {'volume':.015},{'fast':30,'slow':20},{'max_positions':1.5},
                    {'timeframe':'M2'},{'symbol':'MISSING'},{'slow':None},{'unknown':1}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validated_settings(bad)

    def test_cannot_change_instrument_while_positions_open(self):
        engine = PaperEngine()
        engine.order('buy')
        with self.assertRaises(ValueError):
            engine.configure({'symbol':'EURUSD'})
        self.assertEqual(engine.settings['symbol'],'XAUUSD')

    def test_configuration_changes_do_not_move_existing_stops(self):
        engine = PaperEngine()
        position = engine.order('buy')
        original = position['sl']
        engine.configure({'stop_loss':15})
        self.assertEqual(original,position['sl'])

    def test_stopping_bot_preserves_risk_controls(self):
        engine = PaperEngine()
        engine.order('buy')
        engine.running = False
        p=engine.positions[0]
        close=p['sl']-3
        engine.tick({'time':engine.candles[-1]['time']+60,'open':close,'high':close,
                     'low':close,'close':close,'volume':100})
        self.assertEqual(len(engine.history),1)

    def test_backtest_trades_and_is_deterministic(self):
        first, second = run_backtest({}), run_backtest({})
        self.assertGreater(first['trades'], 0)
        self.assertEqual(first['net'], second['net'])
        self.assertEqual(first['trades'], second['trades'])
        self.assertTrue(math.isfinite(first['drawdown']))
        self.assertEqual(first['data_source'],'synthetic')

    def test_ema_bot_places_orders_without_manual_input(self):
        engine=PaperEngine(seed=2026)
        engine.running=True
        for _ in range(600):
            engine.tick()
        self.assertGreater(len(engine.history),0)
        self.assertTrue(all(t['source']=='EMA sample' for t in engine.history))

    def test_csv_contains_closed_trades(self):
        engine=PaperEngine()
        engine.order('sell')
        engine.close_all()
        csv=engine.csv()
        self.assertIn('gross,fee,net',csv)
        self.assertIn('XAUUSD,sell,0.01',csv)


if __name__=='__main__':
    unittest.main()
