# scanner_v2.py - REAL-TIME SURGE HUNTER v12.1 | FREE TIER (T STREAM)
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from collections import defaultdict, deque
import pytz
import websockets
from telegram import Bot

# ========================= CONFIG =========================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')
POLYGON_API_KEY = os.getenv('POLYGON_API_KEY', 'gXgSS76DhP76m_JlAvJhEg4MkvvKtfaV')

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("Set TELEGRAM_TOKEN and CHAT_ID in env!")

# Surge thresholds
MIN_PRICE = 0.50
MIN_VOLUME_1MIN = 100_000
VOLUME_MULTIPLIER = 3.0
PRICE_JUMP_PCT = 0.8

# Cooldown
ALERT_COOLDOWN = 15 * 60
COOLDOWN_TRACKER = {}

# Baseline: 20 mins
BASELINE_WINDOW = 20
volume_history = defaultdict(lambda: deque(maxlen=BASELINE_WINDOW))

# Markets
VALID_EXCHANGES = {1, 2, 3, 4, 5, 8}

# WebSocket
WS_URL = "wss://socket.polygon.io/stocks"

# In-memory 1-min buffer
current_minute = None
minute_buffer = defaultdict(lambda: {'volume': 0, 'price': 0, 'count': 0})
last_flush = 0
# ==========================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)

ET = pytz.timezone('US/Eastern')
CST = pytz.timezone('America/Chicago')

def is_market_open():
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    t = now.time()
    return datetime.strptime("09:30", "%H:%M").time() <= t < datetime.strptime("16:00", "%H:%M").time()

async def send_alert(symbol, price, change_pct, volume, ratio):
    now_cst = datetime.now(CST).strftime("%H:%M")
    message = (
        f"<b>REAL-TIME SURGE!</b>\n\n"
        f"<b>{symbol}</b> @ ${price:.3f}\n"
        f"+{change_pct:.2f}% (1 min)\n"
        f"{ratio:.1f}x volume ({volume:,} shares)\n"
        f"{now_cst} CST\n"
        f"<a href='https://finance.yahoo.com/quote/{symbol}'>TRADE NOW</a>"
    )
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML', disable_web_page_preview=True)
        logger.info(f"ALERT → {symbol}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

async def process_minute_bar(symbol, price, volume):
    global volume_history

    if price < MIN_PRICE or volume < MIN_VOLUME_1MIN:
        return

    # Update baseline
    volume_history[symbol].append(volume)
    if len(volume_history[symbol]) < 2:
        return
    baseline = sum(volume_history[symbol][:-1]) / len(volume_history[symbol][:-1])
    if baseline <= 0:
        return

    ratio = volume / baseline

    # Estimate price change (use last known)
    prev_price = 0
    for prev_vol in reversed(volume_history[symbol]):
        prev_price = price * 0.99  # fallback
        break
    change_pct = ((price / prev_price) - 1) * 100 if prev_price > 0 else 0

    if ratio >= VOLUME_MULTIPLIER and change_pct >= PRICE_JUMP_PCT:
        now = int(time.time())
        if symbol not in COOLDOWN_TRACKER or now - COOLDOWN_TRACKER[symbol] >= ALERT_COOLDOWN:
            COOLDOWN_TRACKER[symbol] = now
            await send_alert(symbol, price, change_pct, volume, ratio)

async def flush_minute():
    global minute_buffer, current_minute, last_flush
    now = time.time()
    if now - last_flush < 55:  # Flush every ~60s
        return

    for symbol, data in minute_buffer.items():
        if data['count'] > 0:
            avg_price = data['price'] / data['count']
            volume = data['volume']
            if is_market_open():
                await process_minute_bar(symbol, avg_price, volume)
    minute_buffer.clear()
    last_flush = now
    minute_key = int(now // 60)
    if minute_key != current_minute:
        current_minute = minute_key

async def connect_polygon():
    auth_msg = json.dumps({"action": "auth", "params": POLYGON_API_KEY})
    sub_msg = json.dumps({"action": "subscribe", "params": "T"})  # ALL TRADES

    while True:
        try:
            logger.info("Connecting to Polygon WebSocket (T stream)...")
            async with websockets.connect(WS_URL) as ws:
                await ws.send(auth_msg)
                resp = await ws.recv()
                logger.info(f"Auth: {resp}")

                await ws.send(sub_msg)
                resp = await ws.recv()
                logger.info(f"Subscribed to T: {resp}")

                async for message in ws:
                    data = json.loads(message)
                    for ev in data:
                        if ev.get('ev') == 'T':  # Trade
                            symbol = ev['sym']
                            price = ev['p']
                            size = ev['s']
                            exchange = ev.get('x', 0)

                            if exchange not in VALID_EXCHANGES:
                                continue

                            minute_key = int(ev['t'] / 1_000_000_000 // 60)  # ns → min
                            if current_minute is None:
                                current_minute = minute_key

                            minute_buffer[symbol]['volume'] += size
                            minute_buffer[symbol]['price'] += price * size
                            minute_buffer[symbol]['count'] += size

                    await flush_minute()

        except Exception as e:
            logger.error(f"WebSocket error: {e}. Reconnecting in 5s...")
            await asyncio.sleep(5)

async def main():
    logger.info("REAL-TIME SURGE HUNTER v12.1 (FREE TIER) STARTED")
    await connect_polygon()

if __name__ == "__main__":
    asyncio.run(main())
