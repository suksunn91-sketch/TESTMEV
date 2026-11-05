"""Read-only MT5 market feed. All prices and bars come from the logged-in terminal."""
import importlib
import math
import platform
import time

FRAMES = {"M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15"}


class MT5Bridge:
    def __init__(self):
        self.mt5 = None
        self.connected = False
        self.error = None
        self.symbol = "XAUUSDm"
        self.timeframe = "M1"

    def connect(self, path="", symbol="XAUUSDm", timeframe="M1"):
        if platform.system() != "Windows":
            raise ValueError("ราคาจริงต้องใช้ Windows และเปิด Exness MT5 ที่ล็อกอินแล้ว")
        if timeframe not in FRAMES:
            raise ValueError("ช่วงเวลาไม่รองรับ")
        if path and not path.lower().endswith(".exe"):
            raise ValueError("เลือกพาธ terminal64.exe ของ MT5")
        try:
            mt5 = importlib.import_module("MetaTrader5")
        except ImportError:
            raise ValueError("ไม่พบ MetaTrader5 runtime กรุณาใช้ชุด Windows Portable หรือติดตั้ง requirements-mt5.txt") from None
        self.disconnect()
        ok = mt5.initialize(path, timeout=10000) if path else mt5.initialize(timeout=10000)
        if not ok:
            raise ValueError(f"เชื่อม MT5 ไม่สำเร็จ กรุณาเปิดและล็อกอินก่อน (รหัส {mt5.last_error()[0]})")
        if mt5.account_info() is None:
            mt5.shutdown()
            raise ValueError("กรุณาล็อกอินบัญชี Exness ในโปรแกรม MT5 ก่อน")
        if not mt5.symbol_select(symbol, True):
            mt5.shutdown()
            raise ValueError("ไม่พบสินทรัพย์ ลองชื่อตรงกับ Market Watch เช่น XAUUSDm / XAUUSDc")
        self.mt5, self.connected = mt5, True
        self.symbol, self.timeframe = symbol, timeframe
        return self.snapshot()

    def unavailable(self, error=None):
        return {"connected": False, "source": "mt5", "read_only": True,
                "symbol": self.symbol, "timeframe": self.timeframe, "error": error,
                "candles": [], "observed_at": time.time()}

    def rates(self, count=300, closed_only=False):
        frame = getattr(self.mt5, FRAMES[self.timeframe])
        values = self.mt5.copy_rates_from_pos(self.symbol, frame, 1 if closed_only else 0, count)
        if values is None:
            return []
        result = []
        for r in values:
            bar = {"time": int(r["time"]), "open": float(r["open"]), "high": float(r["high"]),
                   "low": float(r["low"]), "close": float(r["close"]),
                   "volume": int(r["tick_volume"]), "spread": int(r["spread"])}
            prices = [bar[k] for k in ("open", "high", "low", "close")]
            if (bar["time"] <= 0 or any(not math.isfinite(p) or p <= 0 for p in prices)
                    or bar["high"] < max(bar["open"], bar["close"])
                    or bar["low"] > min(bar["open"], bar["close"]) or bar["spread"] < 0):
                raise ValueError("ข้อมูลแท่งเทียนจาก MT5 ไม่สมบูรณ์ กรุณาโหลดกราฟใน MT5 ใหม่")
            if result and bar["time"] <= result[-1]["time"]:
                raise ValueError("ลำดับเวลาแท่งเทียน MT5 ไม่ถูกต้อง")
            result.append(bar)
        return result

    def snapshot(self):
        if not self.connected:
            return self.unavailable(self.error)
        terminal = self.mt5.terminal_info()
        account = self.mt5.account_info()
        tick = self.mt5.symbol_info_tick(self.symbol)
        info = self.mt5.symbol_info(self.symbol)
        if terminal is None or not terminal.connected or account is None or info is None:
            return self.unavailable("MT5 ขาดการเชื่อมต่อกับโบรกเกอร์ กรุณาตรวจโปรแกรม MT5")
        if tick is None or not all(math.isfinite(v) and v > 0 for v in (tick.bid, tick.ask)) or tick.ask < tick.bid:
            return self.unavailable("MT5 ยังไม่มีราคา Bid/Ask ที่ใช้ได้สำหรับสินทรัพย์นี้")
        bars = self.rates()
        tick_time = float(getattr(tick, "time_msc", 0) or tick.time * 1000) / 1000
        contract = float(info.trade_contract_size)
        if not math.isfinite(contract) or contract <= 0 or info.volume_step <= 0:
            return self.unavailable("ข้อมูลขนาดสัญญา MT5 ไม่สมบูรณ์")
        return {"connected": True, "source": "mt5", "read_only": True,
                "symbol": self.symbol, "timeframe": self.timeframe,
                "account": "••••" + str(account.login)[-4:], "server": account.server,
                "currency": account.currency, "balance": account.balance, "equity": account.equity,
                "profit": account.profit, "demo": account.trade_mode == self.mt5.ACCOUNT_TRADE_MODE_DEMO,
                "bid": float(tick.bid), "ask": float(tick.ask), "tick_time": tick_time,
                "observed_at": time.time(), "digits": info.digits, "point": info.point,
                "contract": contract, "currency_profit": info.currency_profit,
                "volume_min": info.volume_min, "volume_max": info.volume_max, "volume_step": info.volume_step,
                "candles": bars, "error": None if bars else "รอข้อมูลแท่งเทียนจาก MT5"}

    def history(self):
        state = self.snapshot()
        if not state["connected"]:
            raise ValueError(state.get("error") or "กรุณาเชื่อม MT5 ก่อนทดสอบย้อนหลัง")
        return self.rates(800, closed_only=True), state

    def disconnect(self):
        if self.mt5:
            self.mt5.shutdown()
        self.mt5 = None
        self.connected = False
        self.error = None
