import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from trader.server import Application, create_server


class ServerTests(unittest.TestCase):
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
            self.assertEqual(app.engine.settings['symbol'],'XAUUSD')
            self.assertEqual(path.read_text(),'{broken')

    def test_mt5_on_unsupported_platform_has_useful_error(self):
        with patch('trader.mt5_bridge.platform.system',return_value='Linux'):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/connect',{'symbol':'XAUUSDm'})
        self.assertEqual(caught.exception.code,400)
        self.assertIn('Windows',caught.exception.read().decode())

    def test_backtest_endpoint_runs_real_simulation(self):
        _,body,_=self.request('/api/backtest',{})
        result=json.loads(body)
        self.assertGreater(result['trades'],0)
        self.assertEqual(result['bars'],600)


if __name__=='__main__':
    unittest.main()
