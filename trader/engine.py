"""Paper exchange driven exclusively by broker market data. No generated prices."""
from __future__ import annotations

import csv
import io
import math
import re
import time
from datetime import datetime, timezone

DEFAULTS = {"symbol": "XAUUSDm", "timeframe": "M1", "volume": 0.01, "stop_loss": 8.0,
            "take_profit": 12.0, "max_positions": 3, "fast": 9, "slow": 21}
FRAMES = {"M1": 60, "M5": 300, "M15": 900}


def ema(values, period):
    if not values:
        return []
    result = [values[0]]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def validated_settings(data, current=None):
    if not isinstance(data, dict):
        raise ValueError("รูปแบบการตั้งค่าไม่ถูกต้อง")
    result = dict(current or DEFAULTS)
    for key in data:
        if key not in DEFAULTS:
            raise ValueError("ไม่รู้จักการตั้งค่า: " + key)
    result.update(data)
    if (not isinstance(result["symbol"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", result["symbol"])
            or result["timeframe"] not in FRAMES):
        raise ValueError("สินทรัพย์หรือช่วงเวลาไม่รองรับ")
    for key, lo, hi in [("volume", 0.01, 5), ("stop_loss", 0.00001, 100000),
                        ("take_profit", 0.00001, 100000), ("max_positions", 1, 10),
                        ("fast", 2, 100), ("slow", 3, 200)]:
        try:
            number = float(result[key])
        except (TypeError, ValueError):
            raise ValueError("ค่าตัวเลขไม่ถูกต้อง: " + key) from None
        if not math.isfinite(number) or not lo <= number <= hi:
            raise ValueError(f"{key} ต้องอยู่ระหว่าง {lo}–{hi}")
        if key in ("max_positions", "fast", "slow"):
            if not number.is_integer():
                raise ValueError(key + " ต้องเป็นจำนวนเต็ม")
            number = int(number)
        result[key] = number
    if result["fast"] >= result["slow"]:
        raise ValueError("EMA เร็วต้องน้อยกว่า EMA ช้า")
    if abs(result["volume"] * 100 - round(result["volume"] * 100)) > 1e-7:
        raise ValueError("Lot ต้องเพิ่มทีละ 0.01")
    return result


class PaperEngine:
    def __init__(self, settings=None, balance=5000.0, clock=time.time):
        self.settings = validated_settings(settings or {})
        self.clock = clock
        self.market = {"connected": False, "source": "mt5", "candles": []}
        self._closed_bar = None
        self.initial_balance = self.balance = float(balance)
        self.positions = []
        self.history = []
        self.logs = []
        self.running = False
        self.next_id = 1
        self.last_signal = "รอสัญญาณ EMA"
        self.candles = []
        self.equity_curve = [{"time": int(self.clock()), "value": self.balance}]
        self.log("รอราคาจริงจาก MT5 · บัญชี paper $5,000")

    @property
    def instrument(self):
        return self.market

    def quote(self, side):
        return self.market.get("ask" if side == "buy" else "bid")

    def market_status(self):
        result = dict(self.market)
        age = self.clock() - result.get("tick_time", 0)
        heartbeat = self.clock() - result.get("observed_at", 0)
        if not result.get("connected"):
            status = "disconnected"
        elif not self.candles or result.get("bid") is None:
            status = "waiting"
        elif age > 15 or heartbeat > 3 or age < -5:
            status = "stale"
        else:
            status = "live"
        result.update(status=status, tick_age=max(0, age) if result.get("tick_time") else None)
        return result

    def require_price(self):
        if self.market_status()["status"] != "live":
            raise ValueError("ต้องเชื่อม MT5 และได้รับราคาล่าสุดก่อนทำรายการ")
        if self.market.get("currency_profit") != "USD":
            raise ValueError("บัญชี paper รองรับการคำนวณกำไรสินทรัพย์สกุล USD เท่านั้น")

    def pause_if_stale(self):
        if self.running and self.market_status()["status"] != "live":
            self.running = False
            self.log("พักบอท: ราคาจาก MT5 ไม่เป็นปัจจุบัน · กดเริ่มใหม่เมื่อข้อมูลพร้อม")

    def apply_market(self, market):
        if not market.get("connected"):
            self.market.update(connected=False, error=market.get("error"))
            self.pause_if_stale()
            return
        identity = (market.get("symbol"), market.get("timeframe"))
        old_identity = (self.market.get("symbol"), self.market.get("timeframe"))
        if identity != old_identity:
            if self.positions:
                raise ValueError("ปิดออเดอร์ก่อนเปลี่ยนสินทรัพย์หรือช่วงเวลา")
            self._closed_bar = None
        self.market = dict(market)
        self.candles = [dict(c) for c in market.get("candles", [])]
        self.pause_if_stale()
        if self.market_status()["status"] != "live":
            return
        if self.market.get("currency_profit") != "USD":
            self.running = False
            return
        for position in list(self.positions):
            price = self.quote("sell" if position["side"] == "buy" else "buy")
            sign = 1 if position["side"] == "buy" else -1
            if sign * (price - position["sl"]) <= 0:
                self.close(position["id"], "stop loss")
            elif sign * (price - position["tp"]) >= 0:
                self.close(position["id"], "take profit")
        if self.positions and self.equity() <= self.used_margin() * .5:
            self.running = False
            self.close_all()
            self.log("หยุดบอท: margin level paper ต่ำกว่า 50%")
        # MT5's last bar is still forming. Evaluate each CLOSED bar once.
        closed = self.candles[:-1]
        if closed:
            bar_time = closed[-1]["time"]
            if self._closed_bar is not None and bar_time > self._closed_bar and self.running:
                values = [c["close"] for c in closed]
                if len(values) >= self.settings["slow"] + 1:
                    fast, slow = ema(values, self.settings["fast"]), ema(values, self.settings["slow"])
                    signal = ("buy" if fast[-2] <= slow[-2] and fast[-1] > slow[-1] else
                              "sell" if fast[-2] >= slow[-2] and fast[-1] < slow[-1] else None)
                    self.last_signal = "รอสัญญาณ EMA แท่งปิด" if signal is None else signal.upper()
                    if signal:
                        try:
                            self.order(signal, "EMA closed bar")
                        except ValueError as exc:
                            self.log(str(exc))
            self._closed_bar = bar_time
        self.equity_curve.append({"time": int(market["tick_time"]), "value": round(self.equity(), 4)})
        self.equity_curve = self.equity_curve[-500:]

    def log(self, message):
        self.logs.insert(0, {"time": datetime.now(timezone.utc).isoformat(timespec="seconds"), "message": message})
        self.logs = self.logs[:150]

    def pnl(self, position):
        exit_price = self.quote("sell" if position["side"] == "buy" else "buy")
        return (exit_price - position["entry"]) * (1 if position["side"] == "buy" else -1) * position["volume"] * position["contract"]

    def equity(self):
        return self.balance + sum(self.pnl(p) for p in self.positions)

    def used_margin(self):
        return sum(p["entry"] * p["contract"] * p["volume"] / 100 for p in self.positions)

    def order(self, side, source="manual"):
        self.require_price()
        if side not in ("buy", "sell"):
            raise ValueError("คำสั่งต้องเป็น buy หรือ sell")
        if len(self.positions) >= self.settings["max_positions"]:
            raise ValueError("ถึงจำนวนออเดอร์สูงสุดแล้ว")
        volume = self.settings["volume"]
        minimum, maximum, step = (self.market[k] for k in ("volume_min", "volume_max", "volume_step"))
        if not minimum <= volume <= maximum or abs(volume / step - round(volume / step)) > 1e-6:
            raise ValueError(f"Lot ของสินทรัพย์นี้ต้องอยู่ระหว่าง {minimum}–{maximum} เพิ่มทีละ {step}")
        price = self.quote(side)
        margin = price * self.instrument["contract"] * self.settings["volume"] / 100
        if self.equity() - self.used_margin() < margin:
            raise ValueError("เงินคงเหลือจำลองไม่พอสำหรับออเดอร์นี้")
        sign = 1 if side == "buy" else -1
        position = {"id": self.next_id, "symbol": self.market["symbol"], "side": side, "digits": self.market["digits"],
                    "volume": self.settings["volume"], "entry": price,
                    "sl": price - sign * self.settings["stop_loss"],
                    "tp": price + sign * self.settings["take_profit"],
                    "contract": self.instrument["contract"], "source": source,
                    "opened_at": self.market["tick_time"]}
        self.next_id += 1
        self.positions.append(position)
        self.log(f"เปิด {side.upper()} #{position['id']} · {position['symbol']} · {position['volume']:.2f} lot (จำลอง)")
        return position

    def close(self, position_id, reason="manual"):
        self.require_price()
        position = next((p for p in self.positions if p["id"] == position_id), None)
        if position is None:
            raise ValueError("ไม่พบออเดอร์ที่เปิดอยู่")
        gross = self.pnl(position)
        fee = 7 * position["volume"]
        net = gross - fee
        self.balance += net
        self.positions.remove(position)
        trade = {**position, "exit": self.quote("sell" if position["side"] == "buy" else "buy"),
                 "gross": round(gross, 5), "fee": round(fee, 5), "net": round(net, 5),
                 "closed_at": self.market["tick_time"], "reason": reason}
        self.history.insert(0, trade)
        self.log(f"ปิด #{position_id} · {reason} · {net:+.2f} USD (จำลอง)")
        return trade

    def close_all(self):
        for position in list(self.positions):
            self.close(position["id"], "close all")

    def configure(self, data):
        if self.running:
            raise ValueError("หยุดบอทก่อนเปลี่ยนการตั้งค่า")
        new = validated_settings(data, self.settings)
        changed_market = any(new[k] != self.settings[k] for k in ("symbol", "timeframe"))
        if changed_market and self.positions:
            raise ValueError("ปิดออเดอร์จำลองก่อนเปลี่ยนสินทรัพย์หรือช่วงเวลา")
        self.settings = new
        if changed_market:
            self.candles = []
            self.market = {"connected": False, "source": "mt5", "candles": []}
            self._closed_bar = None
        self.log("บันทึกการตั้งค่าแล้ว")

    def snapshot(self):
        self.pause_if_stale()
        closed = self.history
        return {"mode": "paper", "running": self.running, "settings": self.settings,
                "balance": self.balance, "equity": self.equity(), "margin": self.used_margin(),
                "free_margin": self.equity() - self.used_margin(),
                "unrealized": sum(self.pnl(p) for p in self.positions),
                "net": self.balance - self.initial_balance, "bid": self.quote("sell"), "ask": self.quote("buy"),
                "digits": self.market.get("digits", 2),
                "spread": (self.quote("buy") - self.quote("sell")) if self.quote("buy") is not None else None,
                "market": self.market_status(),
                "positions": [{**p, "pnl": self.pnl(p)} for p in self.positions],
                "history": closed[:500], "trade_count": len(closed),
                "win_rate": round(100 * sum(t["net"] > 0 for t in closed) / len(closed), 1) if closed else None,
                "logs": self.logs, "candles": self.candles, "equity_curve": self.equity_curve,
                "signal": self.last_signal}

    def csv(self):
        output = io.StringIO(newline="")
        fields = ["id", "symbol", "side", "volume", "entry", "exit", "sl", "tp", "gross", "fee", "net", "opened_at", "closed_at", "reason", "source"]
        writer = csv.DictWriter(output, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(reversed(self.history))
        return output.getvalue()


def run_backtest(settings, candles, metadata):
    """Replay real closed MT5 bars. Orders use the next bar's recorded open.

    Bid = recorded open; ask approximated using that bar's historical spread.
    Intrabar fills, execution latency and broker commissions are not reconstructed.
    """
    settings = validated_settings(settings)
    warmup = settings["slow"] + 2
    if len(candles) < warmup + 2:
        raise ValueError("ข้อมูลย้อนหลัง MT5 ไม่เพียงพอ กรุณาเปิดกราฟใน MT5 แล้วลองใหม่")
    now = [candles[warmup]["time"]]
    engine = PaperEngine(settings, clock=lambda: now[0])
    peak, drawdown = 5000.0, 0
    for i in range(warmup, len(candles)):
        now[0] = candles[i]["time"]
        candle = candles[i]
        # Use only the next bar OPEN for execution, never its unseen closing price.
        forming = dict(candle, high=candle["open"], low=candle["open"], close=candle["open"])
        engine.apply_market({**metadata, "connected": True, "source": "mt5", "observed_at": now[0],
                             "tick_time": now[0], "bid": candle["open"],
                             "ask": candle["open"] + candle["spread"] * metadata["point"],
                             "candles": candles[max(0,i-300):i] + [forming]})
        engine.running = True
        peak = max(peak, engine.equity())
        drawdown = max(drawdown, (peak - engine.equity()) / peak * 100)
    engine.running = False
    engine.close_all()
    drawdown = max(drawdown, (peak - engine.balance) / peak * 100)
    engine.equity_curve[-1]["value"] = engine.balance
    state = engine.snapshot()
    return {"trades": state["trade_count"], "net": state["net"], "win_rate": state["win_rate"],
            "drawdown": round(drawdown, 2), "equity_curve": state["equity_curve"],
            "bars": len(candles), "data_source": "mt5", "settings": settings,
            "from_time": candles[0]["time"], "to_time": candles[-1]["time"],
            "note": "ข้อมูลแท่งปิดจริงจาก MT5 · จำลองเข้าแท่งถัดไปที่ราคาเปิด + spread ย้อนหลัง · ไม่จำลองราคาในแท่ง · คอมมิชชันสมมติ $7/lot"}
