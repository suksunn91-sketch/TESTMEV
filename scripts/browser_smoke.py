"""UI integration test with an explicit mocked terminal; never a production price source."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from unittest.mock import patch
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/"tests")]
from trader.server import Application, create_server
from fixtures import fake_mt5

(ROOT/"artifacts").mkdir(exist_ok=True)
module=fake_mt5()
stale=[False]
def tick(symbol):
    original=module.symbol_info_tick.return_value
    original.time=time.time()-(60 if stale[0] else 0)
    original.time_msc=int(original.time*1000)
    return original
module.symbol_info_tick.side_effect=tick

with tempfile.TemporaryDirectory() as directory, patch("trader.mt5_bridge.platform.system",return_value="Windows"), patch("trader.mt5_bridge.importlib.import_module",return_value=module):
    app=Application(directory)
    server=create_server(app)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    poller=threading.Thread(target=app.poll_mt5,daemon=True)
    thread.start();poller.start()
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.getenv("CHROMIUM_PATH"),headless=True,args=["--no-sandbox"])
            page=browser.new_page(viewport={"width":1440,"height":1040},device_scale_factor=1)
            errors=[]
            page.on("pageerror",lambda e:errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{server.server_port}")
            expect(page.locator("#feed-overlay")).to_be_visible()
            expect(page.locator("#buy-button")).to_be_disabled()
            expect(page.locator("#bid-price")).to_have_text("—")
            assert app.state()["candles"]==[]
            page.screenshot(path=str(ROOT/"artifacts"/"desktop-live-feed-waiting.png"),full_page=True)
            page.locator("#chart-connect").click()
            page.locator("#connect-submit").click()
            expect(page.locator("#connect-dialog")).not_to_be_visible()
            expect(page.locator("#feed-badge")).to_have_text("LIVE · MT5")
            expect(page.locator("#feed-overlay")).not_to_be_visible()
            expect(page.locator("#bid-price")).to_have_text("2,650.00")
            expect(page.locator("#ask-price")).to_have_text("2,650.30")
            page.locator("#buy-button").click()
            expect(page.locator("#positions-body tr")).to_have_count(1)
            page.locator("[data-close-order]").click()
            expect(page.locator("#positions-body tr")).to_have_count(0)
            page.locator("[data-tab=history]").click()
            expect(page.locator("#history-body tr")).to_have_count(1)
            with page.expect_download() as download:
                page.locator(".export-button").click()
            assert "XAUUSDm,buy" in download.value.path().read_text("utf-8-sig")
            page.locator("[data-frame=M5]").click()
            expect(page.locator("#chart-description")).to_contain_text("M5")
            assert app.state()["market"]["timeframe"]=="M5"
            page.locator("#toggle-bot").click()
            expect(page.locator("#bot-status")).to_have_text("กำลังทำงาน")
            stale[0]=True
            expect(page.locator("#feed-badge")).to_have_text("ราคาไม่เป็นปัจจุบัน")
            expect(page.locator("#buy-button")).to_be_disabled()
            expect(page.locator("#bot-status")).to_have_text("หยุดอยู่")
            stale[0]=False
            expect(page.locator("#feed-badge")).to_have_text("LIVE · MT5")
            expect(page.locator("#bot-status")).to_have_text("หยุดอยู่")
            old=app.state()["candles"]
            module.terminal_info.return_value.connected=False
            expect(page.locator("#feed-badge")).to_have_text("MT5 ขาดการเชื่อมต่อ")
            assert app.state()["candles"]==old
            module.terminal_info.return_value.connected=True
            expect(page.locator("#feed-badge")).to_have_text("LIVE · MT5")
            page.locator("[data-view=backtest]").click()
            page.locator("#run-backtest").click()
            expect(page.locator("#backtest-results")).to_be_visible()
            expect(page.locator("#backtest-note")).to_contain_text("MT5")
            page.locator("[data-view=trade]").click()
            page.locator("#settings-button").click()
            page.locator("[name=fast]").fill("7")
            page.locator("#settings-form button[type=submit]").click()
            expect(page.locator("#fast-label")).to_have_text("7")
            page.locator("#change-symbol").click()
            page.locator("#disconnect-mt5").click()
            expect(page.locator("#connection-label")).to_have_text("ยังไม่ได้เชื่อมต่อ")
            page.locator("[data-close=connect-dialog]").click()
            expect(page.locator("#buy-button")).to_be_disabled()
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            page.set_viewport_size({"width":390,"height":844})
            assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
            assert not errors,errors
            print(json.dumps({"passed":["empty chart before MT5","broker bid/ask displayed","paper order/close/CSV","actual timeframe selection","stale tick blocks orders and pauses bot","reconnect stays paused","disconnect retains last bars","MT5 history backtest","settings","responsive layout"],"terminal":"explicit test fixture; not a real Exness account","javascript_errors":errors}))
            browser.close()
    finally:
        app.stop_event.set();server.shutdown();server.server_close()
        thread.join();poller.join(timeout=3)
