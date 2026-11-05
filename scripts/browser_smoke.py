"""Optional UI test: pip install playwright; set CHROMIUM_PATH when needed."""
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1]
(ROOT/'artifacts').mkdir(exist_ok=True)

with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH'),headless=True,args=['--no-sandbox'])
    page=browser.new_page(viewport={'width':1440,'height':1040},device_scale_factor=1)
    errors=[]
    page.on('pageerror',lambda error: errors.append(str(error)))
    page.goto(os.getenv('TESTMEV_URL','http://127.0.0.1:8765'))
    expect(page.locator('#ohlc')).to_contain_text('O ')
    page.locator('#buy-button').click()
    expect(page.locator('#positions-body tr')).to_have_count(1)
    page.locator('[data-close-order]').click()
    expect(page.locator('#positions-body tr')).to_have_count(0)
    page.locator('[data-tab=history]').click()
    expect(page.locator('#history-body tr')).to_have_count(1)
    with page.expect_download() as download:
        page.locator('.export-button').click()
    csv=download.value.path().read_text('utf-8-sig')
    assert 'XAUUSD,buy' in csv
    page.locator('#toggle-bot').click()
    expect(page.locator('#bot-status')).to_have_text('กำลังทำงาน')
    page.locator('#toggle-bot').click()
    expect(page.locator('#bot-status')).to_have_text('หยุดอยู่')
    page.locator('#settings-button').click()
    page.locator('[name=fast]').fill('7')
    page.locator('#settings-form button[type=submit]').click()
    expect(page.locator('#settings-dialog')).not_to_be_visible()
    expect(page.locator('#fast-label')).to_have_text('7')
    page.locator('[data-view=backtest]').click()
    page.locator('#run-backtest').click()
    expect(page.locator('#backtest-results')).to_be_visible()
    expect(page.locator('#backtest-note')).to_contain_text('600')
    page.locator('[data-view=trade]').click()
    page.locator('#open-connect').click()
    page.locator('#connect-submit').click()
    expect(page.locator('#connect-error')).to_contain_text('Windows')
    page.locator('[data-close=connect-dialog]').click()
    page.locator('[data-tab=positions]').click()
    page.locator('#settings-button').click()
    page.locator('[name=fast]').fill('9')
    page.locator('#settings-form button[type=submit]').click()
    expect(page.locator('#settings-dialog')).not_to_be_visible()
    page.locator('#toast').wait_for(state='hidden',timeout=8000)
    page.screenshot(path=str(ROOT/'artifacts'/'desktop-preview.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    page.set_viewport_size({'width':390,'height':844})
    page.screenshot(path=str(ROOT/'artifacts'/'mobile-preview.png'),full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile horizontal overflow'
    assert not errors,errors
    print(json.dumps({'passed':['buy','close','history','CSV download','start/stop bot','settings','backtest','MT5 platform feedback','desktop layout','mobile layout'],'console_errors':errors}))
    browser.close()
