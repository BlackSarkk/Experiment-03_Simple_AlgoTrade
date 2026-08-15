"""
Integration smoke test: raw WebSocket connect/reconnect to Binance Futures stream.
Run manually: PYTHONPATH=src .venv/bin/python tests/integration/test_ws.py
Not collected by default pytest run (no test_ prefixed functions at module level).
"""
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src")))

import time
import threading
import websocket

ws_app = None

def on_message(ws, msg):
    print("msg received")

def on_open(ws):
    print("opened")

def on_close(ws, status, msg):
    print("closed")


def run():
    global ws_app
    ws_app = websocket.WebSocketApp(
        "wss://fstream.binance.com/ws/ethusdt@ticker",
        on_message=on_message, on_open=on_open, on_close=on_close
    )
    ws_app.run_forever()


def test_ws_connect_reconnect():
    """Smoke test: WS connects, can be forced closed, reconnects. Runs ~20s."""
    global ws_app
    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(2)
    print("forcing close")
    ws_app.keep_running = False
    if ws_app and ws_app.sock:
        ws_app.sock.close()
    ws_app.close()
    t.join(timeout=5)
    print("reconnecting")
    t2 = threading.Thread(target=run, daemon=True)
    t2.start()
    time.sleep(2)
    ws_app.keep_running = False
    ws_app.close()
    t2.join(timeout=5)


if __name__ == "__main__":
    test_ws_connect_reconnect()
