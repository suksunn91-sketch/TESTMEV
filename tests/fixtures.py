"""Explicit test-only market fixtures; never imported by the application."""
import math
import time
from types import SimpleNamespace
from unittest.mock import Mock


def market(now=None, count=100, symbol="XAUUSDm", timeframe="M1", bid=2650.0, ask=2650.3):
    now = time.time() if now is None else now
    frame = {"M1":60,"M5":300,"M15":900}[timeframe]
    current = int(now // frame) * frame
    bars=[]
    for i in range(count):
        close=2650 + 15 * math.sin(i/6)
        bars.append(dict(time=current-(count-1-i)*frame,open=close-1,high=close+2,low=close-2,
                         close=close,volume=100,spread=30))
    return dict(connected=True,source="mt5",read_only=True,symbol=symbol,timeframe=timeframe,
                bid=bid,ask=ask,tick_time=now,observed_at=now,candles=bars,digits=2,contract=100,
                currency_profit="USD",volume_min=.01,volume_max=100,volume_step=.01,point=.01,
                account="••••5678",server="TEST FIXTURE",currency="USD",balance=5000,equity=5000,
                profit=0,demo=True,error=None)


def fake_mt5():
    module=Mock()
    module.initialize.return_value=True
    module.symbol_select.return_value=True
    module.ACCOUNT_TRADE_MODE_DEMO=0
    module.TIMEFRAME_M1=1
    module.TIMEFRAME_M5=5
    module.TIMEFRAME_M15=15
    module.terminal_info.return_value=SimpleNamespace(connected=True)
    module.account_info.return_value=SimpleNamespace(login=12345678,server="Exness-TestFixture",
        currency="USD",balance=5000,equity=5002,profit=2,trade_mode=0)
    tick_time=time.time()
    module.symbol_info_tick.return_value=SimpleNamespace(bid=2650.,ask=2650.3,time=int(tick_time),time_msc=int(tick_time*1000))
    module.symbol_info.return_value=SimpleNamespace(digits=2,point=.01,trade_contract_size=100,
        currency_profit="USD",volume_min=.01,volume_max=100,volume_step=.01)
    def rates(symbol,frame,start,count):
        m=market(count=min(count,100),symbol=symbol,timeframe={1:"M1",5:"M5",15:"M15"}[frame])
        bars=m["candles"][:-1] if start else m["candles"]
        return [dict(b,tick_volume=b["volume"]) for b in bars]
    module.copy_rates_from_pos.side_effect=rates
    return module
