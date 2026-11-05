"""Read-only MT5 adapter. No order_send, credentials, or trade permission needed."""
import importlib
import platform


class MT5Bridge:
    def __init__(self):
        self.mt5 = None
        self.connected = False
        self.error = None
        self.symbol = "XAUUSD"

    def connect(self, path="", symbol="XAUUSD"):
        if platform.system() != "Windows":
            raise ValueError("การเชื่อม MT5 ต้องใช้ Windows และเปิดโปรแกรม Exness MT5 ไว้ก่อน")
        try:
            mt5 = importlib.import_module("MetaTrader5")
        except ImportError:
            raise ValueError("ยังไม่มีแพ็กเกจ MetaTrader5 ใช้ชุด Windows Portable หรือรัน pip install -r requirements-mt5.txt") from None
        if path and not path.lower().endswith(".exe"):
            raise ValueError("เลือกพาธ terminal64.exe ของ MT5")
        ok = mt5.initialize(path, timeout=10000) if path else mt5.initialize(timeout=10000)
        if not ok:
            raise ValueError("เชื่อม MT5 ไม่สำเร็จ กรุณาเปิด MT5 และเข้าสู่บัญชี Exness ก่อน (" + str(mt5.last_error()[0]) + ")")
        if mt5.account_info() is None:
            mt5.shutdown()
            raise ValueError("ไม่พบบัญชี กรุณาเข้าสู่ระบบในโปรแกรม MT5 ก่อน")
        if not mt5.symbol_select(symbol, True):
            mt5.shutdown()
            raise ValueError("ไม่พบสัญลักษณ์นี้ใน MT5 ลองชื่อที่ตรงกับ Market Watch เช่น XAUUSDm หรือ XAUUSDc")
        self.mt5, self.connected, self.symbol, self.error = mt5, True, symbol, None
        return self.snapshot()

    def snapshot(self):
        if not self.connected:
            return {"connected": False, "read_only": True, "error": self.error}
        account = self.mt5.account_info()
        tick = self.mt5.symbol_info_tick(self.symbol)
        if account is None or tick is None:
            self.error = "ข้อมูล MT5 ขาดการเชื่อมต่อ กรุณาตรวจสอบโปรแกรม MT5"
            return {"connected": False, "read_only": True, "error": self.error}
        rates = self.mt5.copy_rates_from_pos(self.symbol, self.mt5.TIMEFRAME_M1, 0, 160)
        info = self.mt5.symbol_info(self.symbol)
        return {"connected": True, "read_only": True, "symbol": self.symbol,
                "account": "••••" + str(account.login)[-4:], "server": account.server,
                "currency": account.currency, "balance": account.balance,
                "equity": account.equity, "profit": account.profit,
                "demo": account.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO,
                "bid": tick.bid, "ask": tick.ask, "tick_time": tick.time,
                "digits": info.digits if info else 2,
                "candles": [] if rates is None else [
                    {"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                     "low": float(r["low"]), "close": float(r["close"]), "volume": int(r["tick_volume"])} for r in rates]}

    def disconnect(self):
        if self.mt5:
            self.mt5.shutdown()
        self.connected = False
        self.error = None
