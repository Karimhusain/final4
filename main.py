import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import requests
import time
import logging # Import logging module

# === Setup Logging ===
# Konfigurasi logging agar pesan error atau informasi penting tercatat
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("crypto_analyzer.log"), # Simpan log ke file
                        logging.StreamHandler() # Tampilkan juga di konsol
                    ])

# === Setup Exchange ===
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'adjustForTimeDifference': True}
})

symbol = 'BTC/USDT'
timeframes = {
    '1h': {
        'limit': 150,
        'ema1': 10, 'ema2': 20,
        'rsi': 14,
        'stoch_k': 5, 'stoch_d': 3,
        'ichimoku_fast': 9, 'ichimoku_medium': 26, 'ichimoku_slow': 52
    },
    '4h': {
        'limit': 200,
        'ema1': 21, 'ema2': 50,
        'rsi': 14,
        'stoch_k': 5, 'stoch_d': 3,
        'ichimoku_fast': 9, 'ichimoku_medium': 26, 'ichimoku_slow': 52
    },
    '1d': {
        'limit': 300,
        'ema1': 50, 'ema2': 100,
        'rsi': 14,
        'stoch_k': 14, 'stoch_d': 3,
        'ichimoku_fast': 9, 'ichimoku_medium': 26, 'ichimoku_slow': 52
    },
}

# === Discord Webhook URL ===
# GANTI INI DENGAN URL WEBHOOK DISCORD ASLI ANDA
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1382392733062271117/kEfkDCCJzkSVNZdfgTHELp17pThnpZEYL-2lJ9QFFiX_T3FnBBhbSlCMThmkqXjR-FYq"

# --- Fungsi Analisa ---
def analyze_tf(tf, settings):
    logging.info(f"Starting analysis for {symbol} on {tf.upper()} timeframe.")
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=settings['limit'])
        if not ohlcv:
            logging.warning(f"[{tf}] No OHLCV data fetched for {symbol}. Skipping analysis.")
            return None

        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')

        # Ensure df has enough data for indicators
        required_data_points = max(settings['ema2'], settings['rsi'], settings['stoch_k'], settings['ichimoku_slow'])
        if len(df) < required_data_points:
             logging.warning(f"[{tf}] Not enough data ({len(df)} points) for full indicator calculation. Minimum needed: {required_data_points}. Skipping analysis.")
             return None

        # EMA
        df['ema1'] = ta.ema(df['close'], length=settings['ema1'])
        df['ema2'] = ta.ema(df['close'], length=settings['ema2'])

        # RSI
        df['rsi'] = ta.rsi(df['close'], length=settings['rsi'])

        # Stochastic
        stoch = ta.stoch(df['high'], df['low'], df['close'], k=settings['stoch_k'], d=settings['stoch_d'])
        stoch_k_col = f'STOCHk_{settings["stoch_k"]}_{settings["stoch_d"]}_{settings["stoch_d"]}'
        stoch_d_col = f'STOCHd_{settings["stoch_k"]}_{settings["stoch_d"]}_{settings["stoch_d"]}'

        if stoch_k_col in stoch.columns and stoch_d_col in stoch.columns:
            df['stoch_k'] = stoch[stoch_k_col]
            df['stoch_d'] = stoch[stoch_d_col]
        else:
            df['stoch_k'] = stoch.iloc[:, 0] if stoch.shape[1] > 0 else float('nan')
            df['stoch_d'] = stoch.iloc[:, 1] if stoch.shape[1] > 1 else float('nan')
            logging.warning(f"[{tf}] Stochastic columns not found by specific names, using default first/second columns.")


        # Ichimoku Kumo
        ichi = ta.ichimoku(df['high'], df['low'], df['close'])
        df['senkou_a'] = ichi['ISA_9']
        df['senkou_b'] = ichi['ISB_26']

        # Ambil data terakhir
        last = df.iloc[-1]
        
        # --- Price Action Analysis ---
        price_action_info = ""
        # Check if the candle is bullish or bearish
        if last['close'] > last['open']:
            candle_type = "Bullish Candle 🟢"
            # How close is the close price to the high?
            if last['high'] - last['low'] > 0: # Avoid division by zero
                close_to_high_ratio = (last['close'] - last['low']) / (last['high'] - last['low'])
                if close_to_high_ratio >= 0.8:
                    price_action_info = "Strong bullish momentum (closing near high)."
                elif close_to_high_ratio <= 0.2:
                    price_action_info = "Bullish reversal attempt (long lower wick)."
                else:
                    price_action_info = "Bullish candle."
        elif last['close'] < last['open']:
            candle_type = "Bearish Candle 🔴"
            if last['high'] - last['low'] > 0: # Avoid division by zero
                close_to_low_ratio = (last['close'] - last['low']) / (last['high'] - last['low'])
                if close_to_low_ratio <= 0.2:
                    price_action_info = "Strong bearish momentum (closing near low)."
                elif close_to_low_ratio >= 0.8:
                    price_action_info = "Bearish reversal attempt (long upper wick)."
                else:
                    price_action_info = "Bearish candle."
        else:
            candle_type = "Doji/Neutral Candle ⚪"
            price_action_info = "Indecision in the market."

        # Logic Signal
        trend = 'SIDEWAYS'
        if pd.notna(last['senkou_a']) and pd.notna(last['senkou_b']):
            if last['close'] > last['senkou_a'] and last['close'] > last['senkou_b']:
                trend = 'BULLISH'
            elif last['close'] < last['senkou_a'] and last['close'] < last['senkou_b']:
                trend = 'BEARISH'

        ema_cross = 'Death Cross (Bearish)'
        if pd.notna(last['ema1']) and pd.notna(last['ema2']):
            if last['ema1'] > last['ema2']:
                ema_cross = 'Golden Cross (Bullish)'

        stoch_trend = 'Bearish Cross'
        if pd.notna(last['stoch_k']) and pd.notna(last['stoch_d']):
            if last['stoch_k'] > last['stoch_d']:
                stoch_trend = 'Bullish Cross'

        # --- Prepare Discord Embed Message ---
        embed = {
            "title": f"📈 BTC/USDT - {tf.upper()} Analysis",
            "description": f"Update waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}",
            "color": 65280 if trend == "BULLISH" else (16711680 if trend == "BEARISH" else 16776960), # Green, Red, Yellow
            "fields": [
                {"name": "Harga Terakhir", "value": f"`{last['close']:.2f}` USDT", "inline": True},
                {"name": "Jenis Candle", "value": f"{candle_type}", "inline": True},
                {"name": "Price Action", "value": price_action_info, "inline": False},
                {"name": "Tren (Ichimoku)", "value": f"**{trend}**", "inline": True},
                {"name": "EMA Crossover", "value": f"**{ema_cross}**", "inline": True},
                {"name": "RSI", "value": f"`{last['rsi']:.2f}`", "inline": True},
                {"name": "Stochastic", "value": f"K:`{last['stoch_k']:.2f}`, D:`{last['stoch_d']:.2f}` ({stoch_trend})", "inline": True},
                {"name": "Ichimoku Cloud", "value": f"Senkou A: `{last['senkou_a']:.2f}`\nSenkou B: `{last['senkou_b']:.2f}`", "inline": False}
            ],
            "footer": {"text": "Data dari Binance via CCXT & pandas_ta"}
        }

        # Send to Discord using embed
        send_to_discord(embed=embed)

        # Print to console for quick checks (can be more condensed here)
        console_message = f"\n=== [{tf.upper()}] BTC/USDT ===\n" \
                          f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" \
                          f"Price: {last['close']:.2f} ({candle_type}, {price_action_info})\n" \
                          f"Trend (Ichimoku): {trend}\n" \
                          f"EMA {settings['ema1']}/{settings['ema2']}: {ema_cross}\n" \
                          f"RSI: {last['rsi']:.2f}\n" \
                          f"Stochastic K: {last['stoch_k']:.2f}, D: {last['stoch_d']:.2f} ({stoch_trend})\n" \
                          f"===============================\n"
        logging.info(console_message) # Use logging.info for console output

        return last['time'] # Return the timestamp of the last candle fetched

    except ccxt.NetworkError as e:
        logging.error(f"[{tf}] Network error: {e}. Retrying soon...")
        return None
    except ccxt.ExchangeError as e:
        # Handle specific exchange errors like rate limits
        if 'Too many requests' in str(e) or 'limit' in str(e).lower():
            logging.warning(f"[{tf}] Rate limit hit: {e}. Waiting longer before next attempt.")
            time.sleep(exchange.rateLimit / 1000 + 5) # Wait rateLimit duration + 5 seconds buffer
        else:
            logging.error(f"[{tf}] Exchange error: {e}. Check symbol or API limits.")
        return None
    except pd.errors.EmptyDataError:
        logging.warning(f"[{tf}] Empty data received from exchange. Might be temporary API issue or no data.")
        return None
    except Exception as e:
        logging.critical(f"[{tf}] An unexpected critical error occurred: {e}", exc_info=True)
        return None

def send_to_discord(message=None, embed=None):
    """Sends a message or an embed to the configured Discord webhook."""
    if not DISCORD_WEBHOOK_URL or DISCORD_WEBHOOK_URL == "YOUR_DISCORD_WEBHOOK_URL_HERE":
        logging.error("Discord Webhook URL not configured. Please set DISCORD_WEBHOOK_URL.")
        return

    payload = {}
    if message:
        payload["content"] = message
    if embed:
        payload["embeds"] = [embed]

    if not payload: # If no message or embed is provided
        logging.warning("No content or embed provided for Discord message. Skipping.")
        return

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        logging.info("Message sent to Discord successfully!")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message to Discord: {e}", exc_info=True)


# --- Main Execution Loop ---
if __name__ == "__main__":
    logging.info("Starting crypto analysis bot for 1h, 4h, 1d. Updates sent to Discord every 15 minutes if new candle closes.")

    # Dictionary to store the last candle timestamp for each timeframe
    last_processed_candle_time = {tf: None for tf in timeframes.keys()}
    
    # Run all analyses once at startup to populate initial data
    logging.info("\n--- Performing initial analysis for all timeframes ---")
    for tf, settings in timeframes.items():
        processed_time = analyze_tf(tf, settings)
        if processed_time:
            last_processed_candle_time[tf] = processed_time
        time.sleep(exchange.rateLimit / 1000 + 1) # Respect initial rate limits

    logging.info("\nInitial analyses complete. Entering continuous monitoring mode.")

    while True:
        current_time = datetime.now()
        
        # Calculate time to next 15-minute mark
        minutes_to_next_15 = 15 - (current_time.minute % 15)
        if minutes_to_next_15 == 15 and current_time.second == 0:
            minutes_to_next_15 = 0

        next_15m_mark = current_time + timedelta(minutes=minutes_to_next_15)
        next_15m_mark = next_15m_mark.replace(second=0, microsecond=0)

        sleep_duration = (next_15m_mark - current_time).total_seconds()
        sleep_duration += 5 # Add a 5-second buffer to ensure candle close

        logging.info(f"\n[{current_time.strftime('%Y-%m-%d %H:%M:%S WIB')}] Waiting for next 15m interval. Sleeping for {int(sleep_duration)} seconds until {next_15m_mark.strftime('%H:%M:%S WIB')}...")
        time.sleep(sleep_duration)

        # After waking up, check which timeframes need updating
        logging.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}] Checking for new candle closes...")

        for tf, settings in timeframes.items():
            try:
                # Fetch only the latest candle to check its timestamp
                latest_ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=1)
                
                if latest_ohlcv:
                    latest_candle_time_ms = latest_ohlcv[0][0]
                    latest_candle_time = pd.to_datetime(latest_candle_time_ms, unit='ms')

                    # Compare with last processed time, handling initial None state
                    if last_processed_candle_time[tf] is None or latest_candle_time > last_processed_candle_time[tf]:
                        logging.info(f"New {tf.upper()} candle detected! Analyzing and sending to Discord.")
                        processed_time = analyze_tf(tf, settings) # Call analyze_tf to process and send
                        if processed_time:
                            last_processed_candle_time[tf] = processed_time
                    else:
                        logging.info(f"No new {tf.upper()} candle since last check. Last processed: {last_processed_candle_time[tf].strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    logging.warning(f"Could not fetch latest {tf.upper()} candle to check for updates.")
            except Exception as e:
                logging.error(f"Error checking {tf.upper()} candle update: {e}", exc_info=True)
            
            time.sleep(exchange.rateLimit / 1000 + 1) # Respect exchange rate limits between fetches

