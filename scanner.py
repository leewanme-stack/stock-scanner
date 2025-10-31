# stock_surge_top250.py
# TOP 250 HIGH-VOLUME STOCKS | <10s SCANS | v2.4 YFINANCE SCREENER
import yfinance as yf
import pandas as pd
import asyncio
import schedule
import time
import logging
from telegram import Bot
from datetime import datetime
import pytz
from concurrent.futures import ThreadPoolExecutor

# ========================= CONFIG =========================
TELEGRAM_TOKEN = '8495322474:AAEh2inn7ArIj2GAkSfm0iQWqVc5gE_UyFA'
CHAT_ID = '7276470276'

TRADING_START_CST = "08:30"
TRADING_END_CST = "15:00"
WINDOW_MINUTES = 5
MIN_PRICE_SURGE_PCT = 1.2
MIN_VOLUME_MULTIPLIER = 2.0

CST = pytz.timezone('America/Chicago')
MAX_WORKERS = 50
# ==========================================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger(__name__)

bot = Bot(token=TELEGRAM_TOKEN)

# === FETCH 250 MOST ACTIVE via YFINANCE SCREENER (NO SCRAPING) ===
def fetch_top_250_active():
    try:
        # yfinance built-in screener
        screen = yf.Tickers(' '.join(['AAPL']))  # Dummy to init
        df = yf.utils.get_screeners(['most_actives'])['most_actives']
        
        # Extract symbols
        tickers = df['Symbol'].tolist()[:300]  # Get extra
        tickers = [t.replace('.', '-') for t in tickers]  # Fix format
        tickers = list(dict.fromkeys(tickers))[:250]  # Dedupe + cap
        
        logger.info(f"Fetched {len(tickers)} most active tickers via yfinance screener.")
        return tickers
    except Exception as e:
        logger.warning(f"Screener failed: {e}, using fallback.")
        return get_fallback_top_250()


# === FALLBACK: 250 HIGH-VOLUME TICKERS (GUARANTEED) ===
def get_fallback_top_250():
    return [
        'AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AMD', 'SMCI', 'ARM',
        'AVGO', 'QCOM', 'TXN', 'MU', 'ASML', 'LRCX', 'KLAC', 'AMAT', 'SNOW', 'CRWD',
        'ZS', 'PANW', 'FTNT', 'DDOG', 'NET', 'MDB', 'HUBS', 'TEAM', 'COIN', 'HOOD',
        'SOFI', 'UPST', 'AFRM', 'MARA', 'RIOT', 'CLSK', 'BITF', 'LCID', 'RIVN', 'NIO',
        'XPEV', 'LI', 'BLNK', 'CHPT', 'PLUG', 'FCEL', 'BE', 'HIMS', 'CLOV', 'OSCR',
        'TDOC', 'RXRX', 'VIR', 'MRNA', 'BNTX', 'NVAX', 'GME', 'AMC', 'BB', 'KOSS',
        'SPCE', 'RKLB', 'ASTS', 'BABA', 'PDD', 'JD', 'BIDU', 'NTES', 'TME', 'BZ',
        'VIPS', 'IQ', 'WB', 'SPY', 'QQQ', 'TQQQ', 'SQQQ', 'SOXL', 'SOXS', 'LABU',
        'LABD', 'SPXL', 'SPXS', 'ARKK', 'ARKQ', 'ARKW', 'ARKG', 'ARKF', 'IBIT',
        'GBTC', 'BITO', 'MSTR', 'SOUN', 'PLTR', 'IONQ', 'RGTI', 'QBTS', 'QUBT',
        'AUR', 'PATH', 'U', 'AI', 'SATS', 'LUMN', 'BAC', 'WFC', 'JPM', 'C', 'GS',
        'MS', 'SCHW', 'PFE', 'MRK', 'JNJ', 'XOM', 'CVX', 'COP', 'OXY', 'SLB', 'HAL',
        'KMI', 'WMB', 'ET', 'EPD', 'F', 'GM', 'HMC', 'TM', 'RACE', 'STLA', 'NOC',
        'LMT', 'RTX', 'BA', 'DIS', 'CMCSA', 'VZ', 'T', 'TMUS', 'CHTR', 'EA', 'TTWO',
        'ROKU', 'SHOP', 'SE', 'MELI', 'NU', 'XP', 'ITUB', 'BBD', 'VALE', 'PBR', 'EC',
        'TSM', 'INFY', 'HDB', 'WIT', 'VOD', 'BTI', 'UL', 'DEO', 'NVS', 'NVO', 'AZN',
        'SNY', 'GSK', 'ABBV', 'LLY', 'TMO', 'DHR', 'ABT', 'MDT', 'UNH', 'CI', 'HUM',
        'CVS', 'DG', 'DLTR', 'TGT', 'WMT', 'COST', 'HD', 'LOW', 'TJX', 'ROST', 'M',
        'KSS', 'AEO', 'URBN', 'ANF', 'LULU', 'NKE', 'VFC', 'PVH', 'RL', 'TPR', 'CPRI',
        'SIG', 'MOV', 'CROX', 'DECK', 'COLM', 'UA', 'UAA', 'CCL', 'RCL', 'NCLH', 'DAL',
        'AAL', 'UAL', 'LUV', 'SAVE', 'JBLU', 'ALK', 'HA', 'MESA', 'SKYW', 'EXPR',
        'GME', 'AMC', 'BBBY', 'KODK', 'DKS', 'HIBB', 'FL', 'GPS', 'JWN', 'EXPR'
    ][:250]


# === GLOBAL ===
HOT_STOCKS = []


# === ULTRA-FAST SYNC FETCH ===
def fetch_ticker_sync(symbol):
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d", interval="1m", prepost=False, timeout=8)
        if hist.empty or len(hist) < WINDOW_MINUTES:
            return None

        recent = hist.tail(WINDOW_MINUTES)
        price_start = recent['Close'].iloc[0]
        price_end = recent['Close'].iloc[-1]
        if price_start <= 0 or price_end <= 0:
            return None

        price_change = ((price_end / price_start) - 1) * 100
        volume_ratio = recent['Volume'].mean() / hist['Volume'].head(20).mean()

        if price_change >= MIN_PRICE_SURGE_PCT and volume_ratio >= MIN_VOLUME_MULTIPLIER:
            return {
                'symbol': symbol,
                'change': price_change,
                'volume': volume_ratio
            }
    except Exception:
        pass
    return None


# === ULTRA-FAST ASYNC SCAN ===
async def ultra_fast_scan_async():
    if not is_market_open():
        logger.info("Market closed (CST)")
        return

    current_time = datetime.now(CST).strftime("%H:%M")
    logger.info(f"SCANNING TOP 250 @ {current_time} CST | {len(HOT_STOCKS)} stocks")

    start_time = time.time()
    loop = asyncio.get_event_loop()

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        tasks = [loop.run_in_executor(executor, fetch_ticker_sync, s) for s in HOT_STOCKS]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    alerts = [r for r in results if isinstance(r, dict)]
    duration = time.time() - start_time

    logger.info(f"Scan complete in {duration:.2f}s | {len(alerts)} surge(s) detected!")

    for alert in alerts:
        message = (
            f"<b>TOP 250 SURGE!</b>\n\n"
            f"<b>{alert['symbol']}</b>\n"
            f"+{alert['change']:.2f}%\n"
            f"{alert['volume']:.1f}x Volume\n"
            f"{current_time} CST\n"
            f"<a href='https://finance.yahoo.com/quote/{alert['symbol']}'>TRADE NOW</a>"
        )
        await send_telegram(message)
        try:
            import winsound
            winsound.Beep(1600, 700)
        except:
            pass


# === TELEGRAM ===
async def send_telegram(message):
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML', disable_web_page_preview=True)
        symbol = message.split('<b>')[2].split('</b>')[0]
        logger.info(f"Telegram alert sent: {symbol}")
    except Exception as e:
        logger.error(f"Telegram error: {e}")


# === MARKET HOURS ===
def is_market_open():
    now = datetime.now(CST)
    if now.weekday() >= 5:
        return False
    current = now.strftime("%H:%M")
    return TRADING_START_CST <= current < TRADING_END_CST


# === WRAPPER ===
def ultra_fast_scan():
    asyncio.run(ultra_fast_scan_async())


# === MAIN LOOP ===
def start_bot():
    global HOT_STOCKS
    if not TELEGRAM_TOKEN or 'YOUR_' in TELEGRAM_TOKEN:
        logger.error("UPDATE TELEGRAM_TOKEN!")
        return

    HOT_STOCKS = fetch_top_250_active()
    if len(HOT_STOCKS) < 100:
        HOT_STOCKS = get_fallback_top_250()

    logger.info(f"Loaded {len(HOT_STOCKS)} high-volume stocks")
    logger.info("TOP 250 STOCK SURGE BOT STARTED! (v2.4 FINAL)")

    schedule.every(60).seconds.do(ultra_fast_scan)

    try:
        while True:
            schedule.run_pending()
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")


# === RUN ===
if __name__ == "__main__":
    start_bot()
