"""Run with Python 3.11+, or open TESTMEV.exe on Windows."""
import argparse
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from trader.server import Application, create_server


def open_window(url):
    if sys.platform == "win32":
        for variable in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            base = os.environ.get(variable)
            if base:
                edge = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
                if edge.exists():
                    subprocess.Popen([str(edge), "--app=" + url, "--window-size=1440,960"])
                    return
    webbrowser.open(url)


def main():
    parser = argparse.ArgumentParser(description="TESTMEV trading workspace · Paper + MT5 read-only")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--smoke-test", action="store_true", help="Validate the packaged Python runtime without opening a window")
    args = parser.parse_args()
    if args.smoke_test:
        import json
        from trader.engine import PaperEngine, run_backtest
        engine = PaperEngine()
        engine.order("buy")
        engine.tick()
        engine.close_all()
        result = run_backtest(engine.settings)
        assert engine.snapshot()["trade_count"] == 1 and result["trades"] > 0
        assert (Path(__file__).parent / "trader" / "static" / "app.js").exists()
        if sys.platform == "win32":
            import MetaTrader5
            import numpy
        print(json.dumps({"ok": True, "python": sys.version.split()[0], "backtest_trades": result["trades"]}))
        return
    app = Application()
    server = create_server(app, args.port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"TESTMEV: {url}\nPaper trading only. MT5 connection is read-only.\nUse the Exit button to close the application.", flush=True)
    workers = [threading.Thread(target=app.run_ticks, daemon=True), threading.Thread(target=app.poll_mt5, daemon=True)]
    for worker in workers:
        worker.start()
    if not args.no_browser:
        threading.Timer(0.5, open_window, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        app.stop_event.set()
        for worker in workers:
            worker.join(timeout=12)
        with app.lock:
            # Stop the simulator and settle positions before archiving this session.
            app.engine.running = False
            app.engine.close_all()
            app.archive_session()
        with app.mt5_lock:
            app.bridge.disconnect()
        server.server_close()


if __name__ == "__main__":
    main()
