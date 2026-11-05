import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from trader.server import Application, create_server
from trader.engine import PaperEngine
from fixtures import market, fake_mt5


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.app.engine = PaperEngine()
        self.app.engine.apply_market(market())

    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.app=Application(cls.temp.name)
        cls.server=create_server(cls.app)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
        cls.url=f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self,path,data=None,headers=None):
        headers=headers or {}
        if data is not None:
            data=json.dumps(data).encode()
            headers={'X-App-Token':self.app.token,'Content-Type':'application/json',**headers}
        with urlopen(Request(self.url+path,data=data,headers=headers),timeout=5) as response:
            return response.status,response.read(),response.headers

    def test_page_and_state(self):
        status,body,headers=self.request('/')
        self.assertEqual(status,200)
        self.assertIn('TESTMEV',body.decode())
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        status,body,_=self.request('/api/state')
        self.assertEqual(json.loads(body)['mode'],'paper')

    def test_tradingview_guide_and_strategy_download(self):
        _,body,headers=self.request('/')
        self.assertIn('EIGHTCAP%3AXAUUSD',body.decode())
        self.assertIn('frame-src https://s.tradingview.com',headers['Content-Security-Policy'])
        _,pine,headers=self.request('/strategy.pine')
        self.assertIn('//@version=6',pine.decode())
        self.assertIn('strategy.exit',pine.decode())
        self.assertIn('attachment',headers['Content-Disposition'])

    def test_csrf_token_required(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/order',{'side':'buy'},{'X-App-Token':'wrong'})
        self.assertEqual(caught.exception.code,403)

    def test_foreign_origin_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/order',{'side':'buy'},{'Origin':'https://example.com'})
        self.assertEqual(caught.exception.code,403)

    def test_dns_rebinding_host_rejected(self):
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/state',headers={'Host':'evil.example'})
        self.assertEqual(caught.exception.code,403)

    def test_order_close_export_roundtrip(self):
        _,body,_=self.request('/api/order',{'side':'buy'})
        position=json.loads(body)['positions'][-1]
        _,body,_=self.request('/api/close',{'id':position['id']})
        self.assertTrue(any(t['id']==position['id'] for t in json.loads(body)['history']))
        _,body,headers=self.request('/api/export')
        self.assertIn('XAUUSD',body.decode('utf-8-sig'))
        self.assertIn('attachment',headers['Content-Disposition'])

    def test_settings_saved_without_credentials(self):
        self.request('/api/settings',{'fast':8})
        saved=json.loads((Path(self.temp.name)/'settings.json').read_text())
        self.assertEqual(saved['fast'],8)
        self.assertNotIn('password',saved)

    def test_invalid_settings_do_not_corrupt_disk(self):
        self.request('/api/settings',{'slow':22})
        before=(Path(self.temp.name)/'settings.json').read_bytes()
        with self.assertRaises(HTTPError):
            self.request('/api/settings',{'volume':-1})
        self.assertEqual(before,(Path(self.temp.name)/'settings.json').read_bytes())

    def test_corrupt_settings_fall_back_without_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'settings.json'
            path.write_text('{broken')
            app=Application(temp)
            self.assertEqual(app.engine.settings['symbol'],'XAUUSDm')
            self.assertEqual(path.read_text(),'{broken')

    def test_mt5_on_unsupported_platform_has_useful_error(self):
        with patch('trader.mt5_bridge.platform.system',return_value='Linux'):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/connect',{'symbol':'XAUUSDm'})
        self.assertEqual(caught.exception.code,400)
        self.assertIn('Windows',caught.exception.read().decode())

    def test_backtest_endpoint_runs_real_simulation(self):
        feed=market()
        with patch.object(self.app.bridge,'history',return_value=(feed['candles'],feed)):
            _,body,_=self.request('/api/backtest',{})
        result=json.loads(body)
        self.assertGreater(result['trades'],0)
        self.assertEqual(result['bars'],100)
        self.assertEqual(result['data_source'],'mt5')

    def test_offline_application_has_empty_chart_and_blocks_trade(self):
        self.app.engine = PaperEngine()
        _,body,_=self.request('/api/state')
        self.assertEqual(json.loads(body)['candles'],[])
        self.assertIsNone(json.loads(body)['bid'])
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/order',{'side':'buy'})
        self.assertEqual(caught.exception.code,400)

    def test_connect_route_populates_main_chart_from_adapter(self):
        module=fake_mt5()
        with patch('trader.mt5_bridge.platform.system',return_value='Windows'),patch('trader.mt5_bridge.importlib.import_module',return_value=module):
            _,body,_=self.request('/api/connect',{'symbol':'XAUUSDm'})
        state=json.loads(body)
        self.assertEqual(state['bid'],2650.)
        self.assertEqual(state['candles'],state['mt5']['candles'])
        self.assertEqual(state['market']['status'],'live')
        self.assertTrue((Path(self.temp.name)/'connection.json').exists())

    def test_poll_exception_freezes_chart_and_pauses_bot(self):
        old=list(self.app.engine.candles)
        self.app.engine.running=True
        with patch.object(self.app.bridge,'snapshot',side_effect=RuntimeError('offline')):
            self.app.poll_once()
        self.assertFalse(self.app.engine.running)
        self.assertEqual(self.app.engine.candles,old)
        self.assertEqual(self.app.state()['market']['status'],'disconnected')

    def test_session_archive_does_not_fabricate_exit_fills(self):
        self.app.engine.order('buy')
        self.app.engine.apply_market({'connected':False})
        self.app.archive_session()
        self.assertEqual(len(self.app.engine.positions),1)
        self.assertEqual(self.app.engine.history,[])
        self.assertTrue(list(Path(self.temp.name).glob('open-paper-*.json')))

    def test_timeframe_change_requests_new_broker_history(self):
        with patch.object(self.app.bridge,'snapshot',return_value=market(timeframe='M5')):
            _,body,_=self.request('/api/settings',{'timeframe':'M5'})
        state=json.loads(body)
        self.assertEqual(state['market']['timeframe'],'M5')
        self.assertEqual(self.app.bridge.timeframe,'M5')


if __name__=='__main__':
    unittest.main()
