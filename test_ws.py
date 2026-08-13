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
    ws_app = websocket.WebSocketApp("wss://fstream.binance.com/ws/ethusdt@miniTicker", on_message=on_message, on_open=on_open, on_close=on_close)
    ws_app.run_forever()

t = threading.Thread(target=run)
t.start()
time.sleep(10)
print("forcing close")
ws_app.keep_running = False
if ws_app.sock:
    ws_app.sock.close()
ws_app.close()
t.join()
print("reconnecting")
t2 = threading.Thread(target=run)
t2.start()
time.sleep(10)
ws_app.keep_running = False
ws_app.close()
t2.join()
