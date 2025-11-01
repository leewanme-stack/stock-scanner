# app.py - SURGE HUNTER DASHBOARD v13.0 | SIMULATION MODE
import asyncio
import json
import logging
import os
import time
import random
from datetime import datetime
from collections import defaultdict, deque
import pytz
import websockets
from telegram import Bot
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

# ========================= CONFIG =========================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
POLYGON_API_KEY = os.getenv('POLYGON_API_KEY', 'gXgSS76DhP76m_JlAvJhEg4MkvvKtfaV')

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("Set TELEGRAM_TOKEN and CHAT_ID!")

MIN_PRICE = 0.50
MIN_VOLUME_1MIN = 100_000
VOLUME_MULTIPLIER = 3.0
PRICE_JUMP_PCT = 0.8
ALERT_COOLDOWN = 15 * 60

WS_URL = "wss://socket.polygon.io/stocks"
VALID_EXCHANGES = {1, 2, 3, 4, 5, 8}

# SIMULATION MODE
SIMULATION = True  # SET TO False FOR LIVE
SIM_INTERVAL = 30  # seconds between fake surges
# ==========================================================

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)
alerts = deque(maxlen=100)
alert_queue = asyncio.Queue()
status = {
    "connected": True,
    "market_open": True,
    "alerts_count": 0,
    "active_symbols": 0
}

ET = pytz.timezone('US/Eastern')
CST = pytz.timezone('America/Chicago')

volume_history = defaultdict(lambda: deque(maxlen=20))
last_prices = {}
cooldown_tracker = {}
minute_buffer = defaultdict(lambda: {'volume': 0, 'price': 0, 'size': 0})
last_flush = 0

def is_market_open():
    return SIMULATION or (datetime.now(ET).weekday() < 5 and 
                          datetime.strptime("09:30", "%H:%M").time() <= datetime.now(ET).time() < datetime.strptime("16:00", "%H:%M").time())

async def send_telegram_alert(symbol, price, change_pct, volume, ratio):
    now_cst = datetime.now(CST).strftime("%H:%M")
    badge = "<b>SIM</b> " if SIMULATION else ""
    message = (
        f"{badge}<b>REAL-TIME SURGE!</b>\n\n"
        f"<b>{symbol}</b> @ ${price:.3f}\n"
        f"+{change_pct:.2f}% (1 min)\n"
        f"{ratio:.1f}x volume ({volume:,} shares)\n"
        f"{now_cst} CST\n"
        f"<a href='https://finance.yahoo.com/quote/{symbol}'>TRADE NOW</a>"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        logger.info(f"TELEGRAM → {symbol} ({'SIM' if SIMULATION else 'LIVE'})")
    except Exception as e:
        logger.error(f"Telegram: {e}")

async def process_minute_bar(symbol, price, volume):
    global status
    if price < MIN_PRICE or volume < MIN_VOLUME_1MIN:
        return

    volume_history[symbol].append(volume)
    if len(volume_history[symbol]) < 2:
        return
    baseline = sum(list(volume_history[symbol])[:-1]) / len(list(volume_history[symbol])[:-1])
    if baseline <= 0:
        return
    vol_ratio = volume / baseline

    change_pct = 0.0
    if symbol in last_prices and last_prices[symbol] > 0:
        change_pct = ((price / last_prices[symbol]) - 1) * 100
    last_prices[symbol] = price

    if vol_ratio >= VOLUME_MULTIPLIER and change_pct >= PRICE_JUMP_PCT:
        now_ts = int(time.time())
        if symbol not in cooldown_tracker or now_ts - cooldown_tracker[symbol] >= ALERT_COOLDOWN:
            cooldown_tracker[symbol] = now_ts
            alert = {
                "symbol": symbol,
                "price": round(price, 3),
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "vol_ratio": round(vol_ratio, 1),
                "time": datetime.now(CST).strftime("%H:%M:%S")
            }
            alerts.append(alert)
            await alert_queue.put(alert)
            await send_telegram_alert(**alert)
            status["alerts_count"] += 1
            logger.info(f"SURGE → {symbol} +{change_pct:.1f}% {vol_ratio:.1f}x")

async def flush_minute():
    global minute_buffer, last_flush, status
    now = time.time()
    if now - last_flush < 55:
        return
    status["active_symbols"] = len([s for s in minute_buffer if minute_buffer[s]['size'] > 0])
    for symbol, data in list(minute_buffer.items()):
        if data['size'] > 0:
            avg_price = data['price'] / data['size']
            tot_volume = data['volume']
            await process_minute_bar(symbol, avg_price, tot_volume)
    minute_buffer.clear()
    last_flush = now

# === SIMULATION: FAKE SURGES ===
async def simulate_surge():
    symbols = ['GME', 'AMC', 'BB', 'CLOV', 'SPRT', 'HOOD', 'PLTR', 'RIVN']
    while SIMULATION:
        await asyncio.sleep(random.uniform(SIM_INTERVAL - 10, SIM_INTERVAL + 20))
        
        symbol = random.choice(symbols)
        base_price = round(random.uniform(1.0, 30.0), 2)
        surge_price = round(base_price * random.uniform(1.03, 1.12), 2)
        volume = random.randint(600_000, 3_000_000)

        # Inject
        minute_buffer[symbol]['volume'] += volume
        minute_buffer[symbol]['price'] += surge_price * volume
        minute_buffer[symbol]['size'] += volume

        logger.info(f"SIM: Injected {symbol} +{((surge_price/base_price)-1)*100:.1f}% | {volume:,} vol")
        await flush_minute()

# === POLYGON (SKIP IN SIM) ===
async def polygon_websocket():
    if SIMULATION:
        logger.info("SIMULATION MODE: Skipping real WebSocket")
        return
    # ... (same as before) ...

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/alerts")
async def get_alerts():
    return {"alerts": list(alerts)}

@app.get("/status")
async def get_status():
    status["market_open"] = is_market_open()
    return status

@app.get("/events")
async def sse_events():
    async def event_gen():
        while True:
            try:
                alert = await asyncio.wait_for(alert_queue.get(), timeout=30.0)
                yield f"data: {json.dumps(alert)}\n\n"
            except asyncio.TimeoutError:
                yield "data: {\"type\": \"heartbeat\"}\n\n"
    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

@app.on_event("startup")
async def startup():
    if SIMULATION:
        asyncio.create_task(simulate_surge())
    else:
        asyncio.create_task(polygon_websocket())

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)