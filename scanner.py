# surge_hunter_v11.0.py - RENDER READY
import yfinance as yf
import asyncio
import schedule
import time
import logging
import os
from telegram import Bot
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor

# ========================= CONFIG =========================
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
CHAT_ID = os.getenv('CHAT_ID')

if not TELEGRAM_TOKEN or not CHAT_ID:
    raise ValueError("Set TELEGRAM_TOKEN and CHAT_ID as Environment Variables!")

TRADING_START_CST = "08:30"
TRADING_END_CST = "15:00"
WINDOW_MINUTES = 5
MIN_PRICE_SURGE_PCT = 0.6
MIN_VOLUME_MULTIPLIER = 1.5
MIN_PRICE = 0.5
MIN_VOLUME_5MIN = 50_000

CST = pytz.timezone('America/Chicago')
MAX_WORKERS = 100

# RELATIVE PATH FOR RENDER
TICKERS_FILE_PATH = './tickers.txt'
# ==========================================================

logging.basicConfig(
    level=logging.INFO, 
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.StreamHandler()  # Render logs to stdout
    ]
)
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)

# === LOAD TICKERS ===
def load_tickers_from_file():
    if not os.path.exists(TICKERS_FILE_PATH):
        raise FileNotFoundError(f"tickers.txt not found in project root!")
    
    try:
        with open(TICKERS_FILE_PATH, 'r', encoding='utf-8') as f:
            raw_tickers = [line.strip() for line in f if line.strip()]
        
        cleaned = [t.strip().upper() for t in raw_tickers 
                  if 1 <= len(t.strip()) <= 5 and t.strip().replace('.', '').isalnum()]
        tickers = sorted(list(set(cleaned)))
        
        logger.info(f"Loaded {len(tickers)} tickers from tickers.txt")
        return tickers
    except Exception as e:
        raise RuntimeError(f"Failed to load tickers: {e}")

ALL_TICKERS = load_tickers_from_file()
LIVE_TICKERS = ALL_TICKERS.copy()

def refresh_tickers():
    global LIVE_TICKERS
    LIVE_TICKERS = load_tickers_from_file()
    logger.info(f"Refreshed: {len(LIVE_TICKERS)} tickers")

# === SURGE DETECTION (UNCHANGED) ===
def detect_surge(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m", prepost=False, timeout=6)
        if hist.empty or len(hist) < WINDOW_MINUTES + 10:
            return None

        recent = hist.tail(WINDOW_MINUTES)
        price_start = recent['Close'].iloc[0]
        price_end = recent['Close'].iloc[-1]
        if price_start <= 0 or price_end <= 0:
            return None

        price_change = ((price_end / price_start) - 1) * 100
        recent_vol = recent['Volume'].mean()
        baseline_vol = hist['Volume'].head(20).mean()
        volume_ratio = recent_vol / baseline_vol if baseline_vol > 0 else 0

        if (price_end >= MIN_PRICE and 
            recent_vol >= MIN_VOLUME_5MIN and 
            price_change >= MIN_PRICE_SURGE_PCT and 
            volume_ratio >= MIN_VOLUME_MULTIPLIER):
            
            return {
                'symbol': symbol,
                'change': price_change,
                'volume': volume_ratio,
                'price': price_end,
                'vol_5min': int(recent_vol)
            }
    except:
        pass
    return None

# === SCAN + ALERTS (UNCHANGED) ===
async def dynamic_surge_scan():
    if not is_market_open():
        return

    current_time = datetime.now(CST).strftime("%H:%M")
    logger.info(f"SCANNING {len(LIVE_TICKERS)} TICKERS @ {current_time} CST")

    start_time = time.time()
    loop = asyncio.get_event_loop()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        tasks = [loop.run_in_executor(executor, detect_surge, s) for s in LIVE_TICKERS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    alerts = [r for r in results if isinstance(r, dict)]
    duration = time.time() - start_time

    logger.info(f"Scan complete in {duration:.1f}s | {len(alerts)} surges!")

    for alert in alerts:
        message = (
            f"<b>SURGE DETECTED!</b>\n\n"
            f"<b>{alert['symbol']}</b> @ ${alert['price']:.3f}\n"
            f"+{alert['change']:.2f}% (5 min)\n"
            f"{alert['volume']:.1f}x volume ({alert['vol_5min']:,} shares)\n"
            f"{current_time} CST\n"
            f"<a href='https://finance.yahoo.com/quote/{alert['symbol']}'>TRADE NOW</a>"
        )
        await send_telegram(message)

async def send_telegram(message):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML', disable_web_page_preview=True)
        symbol = message.split('<b>')[1].split('</b>')[0]
        logger.info(f"🚀 ALERT → {symbol}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")

def is_market_open():
    now = datetime.now(CST)
    if now.weekday() >= 5:
        return False
    current = now.strftime("%H:%M")
    return TRADING_START_CST <= current < TRADING_END_CST

def run_scan():
    asyncio.run(dynamic_surge_scan())

# === MAIN LOOP ===
def start_hunter():
    logger.info("🚀 SURGE HUNTER v11.0 LIVE ON RENDER!")
    refresh_tickers()
    
    schedule.every(60).seconds.do(run_scan)
    schedule.every(30).minutes.do(refresh_tickers)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Hunter stopped.")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

if __name__ == "__main__":
    start_hunter()
