"""Deterministic paper exchange. Never submits orders to a broker."""
from __future__ import annotations

import csv
import io
import math
import random
from datetime import datetime, timezone

INSTRUMENTS = {
    "XAUUSD": {"price": 2650.0, "contract": 100, "spread": 0.30, "noise": 1.4, "digits": 2},
    "EURUSD": {"price": 1.085, "contract": 100000, "spread": 0.00012, "noise": 0.00022, "digits": 5},
    "BTCUSD": {"price": 68500.0, "contract": 1, "spread": 16, "noise": 85, "digits": 2},
}
DEFAULTS = {"symbol": "XAUUSD", "timeframe": "M1", "volume": 0.01, "stop_loss": 8.0,
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
    if result["symbol"] not in INSTRUMENTS or result["timeframe"] not in FRAMES:
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
    def __init__(self, settings=None, seed=42, balance=5000.0):
        self.settings = validated_settings(settings or {})
        self.rng = random.Random(seed)
        self.initial_balance = self.balance = float(balance)
        self.positions = []
        self.history = []
        self.logs = []
        self.running = False
        self.next_id = 1
        self.steps = 0
        self.last_signal = "รอสัญญาณ EMA"
        self.candles = []
        self.equity_curve = []
        self._seed_market()
        self.log("พร้อมใช้งาน · บัญชีจำลอง $5,000")

    @property
    def instrument(self):
        return INSTRUMENTS[self.settings["symbol"]]

    @property
    def mid(self):
        return self.candles[-1]["close"]

    def quote(self, side):
        return self.mid + (1 if side == "buy" else -1) * self.instrument["spread"] / 2

    def _seed_market(self):
        self.candles = []
        price = self.instrument["price"]
        frame = FRAMES[self.settings["timeframe"]]
        now = int(datetime.now(timezone.utc).timestamp())
        for index in range(160):
            close = max(price * 0.1, price + self.rng.gauss(0, self.instrument["noise"]))
            wick = abs(self.rng.gauss(0, self.instrument["noise"] * 0.5))
            self.candles.append({"time": now - (159 - index) * frame, "open": price,
                                 "high": max(price, close) + wick, "low": min(price, close) - wick,
                                 "close": close, "volume": self.rng.randint(100, 1200)})
            price = close
        self.equity_curve = [{"time": now, "value": self.balance}]

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
        if side not in ("buy", "sell"):
            raise ValueError("คำสั่งต้องเป็น buy หรือ sell")
        if len(self.positions) >= self.settings["max_positions"]:
            raise ValueError("ถึงจำนวนออเดอร์สูงสุดแล้ว")
        price = self.quote(side)
        margin = price * self.instrument["contract"] * self.settings["volume"] / 100
        if self.equity() - self.used_margin() < margin:
            raise ValueError("เงินคงเหลือจำลองไม่พอสำหรับออเดอร์นี้")
        sign = 1 if side == "buy" else -1
        position = {"id": self.next_id, "symbol": self.settings["symbol"], "side": side,
                    "volume": self.settings["volume"], "entry": price,
                    "sl": price - sign * self.settings["stop_loss"],
                    "tp": price + sign * self.settings["take_profit"],
                    "contract": self.instrument["contract"], "source": source,
                    "opened_at": self.candles[-1]["time"]}
        self.next_id += 1
        self.positions.append(position)
        self.log(f"เปิด {side.upper()} #{position['id']} · {position['symbol']} · {position['volume']:.2f} lot (จำลอง)")
        return position

    def close(self, position_id, reason="manual"):
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
                 "closed_at": self.candles[-1]["time"], "reason": reason}
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
            self._seed_market()
        self.log("บันทึกการตั้งค่าแล้ว")

    def tick(self, candle=None):
        self.steps += 1
        if candle is None:
            price = self.mid
            close = max(price * 0.1, price + self.rng.gauss(0, self.instrument["noise"]))
            wick = abs(self.rng.gauss(0, self.instrument["noise"] * 0.3))
            candle = {"time": self.candles[-1]["time"] + FRAMES[self.settings["timeframe"]],
                      "open": price, "high": max(price, close) + wick,
                      "low": min(price, close) - wick, "close": close,
                      "volume": self.rng.randint(100, 1200)}
        self.candles.append(candle)
        self.candles = self.candles[-350:]
        # Execute stops at the observed quote, including gaps; no invented best fills.
        for position in list(self.positions):
            price = self.quote("sell" if position["side"] == "buy" else "buy")
            sign = 1 if position["side"] == "buy" else -1
            if sign * (price - position["sl"]) <= 0:
                self.close(position["id"], "stop loss")
            elif sign * (price - position["tp"]) >= 0:
                self.close(position["id"], "take profit")
        if self.positions and self.equity() <= self.used_margin() * 0.5:
            self.running = False
            self.close_all()
            self.log("หยุดบอท: margin level จำลองต่ำกว่า 50%")
        if self.running:
            prices = [c["close"] for c in self.candles]
            fast, slow = ema(prices, self.settings["fast"]), ema(prices, self.settings["slow"])
            signal = ("buy" if fast[-2] <= slow[-2] and fast[-1] > slow[-1] else
                      "sell" if fast[-2] >= slow[-2] and fast[-1] < slow[-1] else None)
            self.last_signal = "รอสัญญาณ EMA" if signal is None else signal.upper()
            if signal:
                try:
                    self.order(signal, "EMA sample")
                except ValueError as exc:
                    self.log(str(exc))
        self.equity_curve.append({"time": candle["time"], "value": round(self.equity(), 4)})
        self.equity_curve = self.equity_curve[-500:]

    def snapshot(self):
        closed = self.history
        return {"mode": "paper", "running": self.running, "settings": self.settings,
                "balance": self.balance, "equity": self.equity(), "margin": self.used_margin(),
                "free_margin": self.equity() - self.used_margin(),
                "unrealized": sum(self.pnl(p) for p in self.positions),
                "net": self.balance - self.initial_balance, "bid": self.quote("sell"), "ask": self.quote("buy"),
                "digits": self.instrument["digits"], "spread": self.instrument["spread"],
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


def run_backtest(settings):
    engine = PaperEngine(settings, seed=2026)
    engine.running = True
    peak = engine.initial_balance
    drawdown = 0
    for _ in range(600):
        engine.tick()
        equity = engine.equity()
        peak = max(peak, equity)
        drawdown = max(drawdown, (peak - equity) / peak * 100)
    engine.close_all()
    drawdown = max(drawdown, (peak - engine.balance) / peak * 100)
    engine.equity_curve[-1]["value"] = engine.balance
    state = engine.snapshot()
    return {"trades": state["trade_count"], "net": state["net"], "win_rate": state["win_rate"],
            "drawdown": round(drawdown, 2), "equity_curve": state["equity_curve"],
            "bars": 600, "data_source": "synthetic", "settings": engine.settings,
            "note": "ข้อมูลสร้างจำลอง 600 แท่ง · EMA ตัวอย่าง · spread + commission · ไม่ใช่ผลตลาดจริง"}
