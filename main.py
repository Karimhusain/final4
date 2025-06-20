import ccxt
import pandas as pd
import pandas_ta as ta
from datetime import datetime, timedelta
import requests
import time
import logging

# === Setup Logging ===
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[
                        logging.FileHandler("crypto_analyzer.log"),
                        logging.StreamHandler()
                    ])

# === Setup Exchange ===
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'adjustForTimeDifference': True}
})

symbol = 'BTC/USDT'
timeframes = {
    '1h': {
        'limit': 300, 
        'ema1': 10, 'ema2': 20,
        'rsi': 14,
        'stoch_k': 5, 'stoch_d': 3,
        'ichimoku_fast': 9, 'ichimoku_medium': 26, 'ichimoku_slow': 52
    },
    '4h': {
        'limit': 500, 
        'ema1': 21, 'ema2': 50,
        'rsi': 14,
        'stoch_k': 5, 'stoch_d': 3,
        'ichimoku_fast': 9, 'ichimoku_medium': 26, 'ichimoku_slow': 52
    },
    '1d': {
        'limit': 700, 
        'ema1': 50, 'ema2': 100,
        'rsi': 14,
        'stoch_k': 14, 'stoch_d': 3,
        'ichimoku_fast': 9, 'ichimoku_medium': 26, 'ichimoku_slow': 52
    },
}

# === Discord Webhook URL ===
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/1382392733062271117/kEfkDCCJzkSVNZdfgTHELp17pThnpZEYL-2lJ9QFFiX_T3FnBBhbSlCMThmkqXjR-FYq"

# --- Fungsi Analisa ---
def analyze_tf(tf, settings):
    """
    Melakukan analisis teknikal untuk satu timeframe.
    Mengembalikan dictionary data analisis dan waktu candle terakhir,
    bukan langsung mengirim ke Discord.
    """
    logging.info(f"Starting analysis for {symbol} on {tf.upper()} timeframe.")
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=settings['limit'])
        if not ohlcv:
            logging.warning(f"[{tf}] No OHLCV data fetched for {symbol}. Skipping analysis.")
            return None, None

        df = pd.DataFrame(ohlcv, columns=['time', 'open', 'high', 'low', 'close', 'volume'])
        df['time'] = pd.to_datetime(df['time'], unit='ms')

        required_data_points_for_ichimoku = settings['ichimoku_slow'] + 26 # Minimum needed for non-NaN senkou A/B at *current* index

        if len(df) < required_data_points_for_ichimoku:
            logging.warning(f"[{tf}] Not enough data ({len(df)} points) for full Ichimoku Cloud calculation (Senkou A/B will be NaN). Minimum needed (approx): {required_data_points_for_ichimoku}.")
            # Lanjutkan eksekusi agar indikator lain tetap bisa dihitung
            # Ichimoku_senkou_a dan _b akan tetap NaN di bagian terakhir jika tidak cukup data

        df['ema1'] = ta.ema(df['close'], length=settings['ema1'])
        df['ema2'] = ta.ema(df['close'], length=settings['ema2'])

        df['rsi'] = ta.rsi(df['close'], length=settings['rsi'])

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

        ichi = ta.ichimoku(df['high'], df['low'], df['close'], append=True) # Tambahkan append=True agar langsung masuk ke DataFrame
        
        # --- PERBAIKAN UNTUK ICHIMOKU CLOUD DIMULAI DI SINI ---
        # Setelah append=True, kolom Ichimoku harusnya ada di df
        # Akses Senkou A dan B dari kolom yang sudah ditambahkan oleh pandas_ta
        # Kita perlu mengambil nilai Senkou A dan B *sebelum* 26 periode terakhir
        # karena mereka diproyeksikan ke depan.

        current_senkou_a = float('nan')
        current_senkou_b = float('nan')

        # Cek apakah kolom-kolom Ichimoku ada di DataFrame
        if f'ISA_{settings["ichimoku_fast"]}' in df.columns and f'ISB_{settings["ichimoku_medium"]}' in df.columns:
            # Senkou A (ISA) diproyeksikan 26 periode ke depan.
            # Jadi, nilai ISA untuk 'sekarang' ada di indeks df.index[-1-26]
            # Pastikan indeks tidak out of bounds
            if len(df) > 26:
                current_senkou_a = df[f'ISA_{settings["ichimoku_fast"]}'].iloc[-1 - 26] # Ambil nilai 26 candle ke belakang
                current_senkou_b = df[f'ISB_{settings["ichimoku_medium"]}'].iloc[-1 - 26] # Ambil nilai 26 candle ke belakang
            else:
                 logging.warning(f"[{tf}] Not enough data to calculate historical Senkou A/B for current candle. Expected {settings['ichimoku_slow']}+26, got {len(df)}.")
        else:
            logging.warning(f"[{tf}] Ichimoku Cloud columns (ISA/ISB) not found in DataFrame after calculation. They will be NaN.")

        # --- PERBAIKAN UNTUK ICHIMOKU CLOUD SELESAI DI SINI ---
        
        last = df.iloc[-1] # Ambil data candle terakhir
        
        price_action_info = ""
        if last['close'] > last['open']:
            candle_type = "Bullish Candle 🟢"
            if last['high'] - last['low'] > 0:
                close_to_high_ratio = (last['close'] - last['low']) / (last['high'] - last['low'])
                if close_to_high_ratio >= 0.8:
                    price_action_info = "Strong bullish momentum (closing near high)."
                elif close_to_high_ratio <= 0.2:
                    price_action_info = "Bullish reversal attempt (long lower wick)."
                else:
                    price_action_info = "Bullish candle."
        elif last['close'] < last['open']:
            candle_type = "Bearish Candle 🔴"
            if last['high'] - last['low'] > 0:
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

        trend = 'SIDEWAYS'
        # Gunakan current_senkou_a/b untuk penentuan tren Ichimoku
        if pd.notna(current_senkou_a) and pd.notna(current_senkou_b):
            if last['close'] > current_senkou_a and last['close'] > current_senkou_b:
                trend = 'BULLISH'
            elif last['close'] < current_senkou_a and last['close'] < current_senkou_b:
                trend = 'BEARISH'

        ema_cross = 'Death Cross (Bearish)'
        if pd.notna(last['ema1']) and pd.notna(last['ema2']):
            if last['ema1'] > last['ema2']:
                ema_cross = 'Golden Cross (Bullish)'

        stoch_trend = 'Bearish Cross'
        if pd.notna(last['stoch_k']) and pd.notna(last['stoch_d']):
            if last['stoch_k'] > last['stoch_d']:
                stoch_trend = 'Bullish Cross'
        
        analysis_data = {
            'tf': tf.upper(),
            'close_price': last['close'],
            'candle_type': candle_type,
            'price_action': price_action_info,
            'trend': trend,
            'ema_cross': ema_cross,
            'rsi': last['rsi'],
            'stoch_k': last['stoch_k'],
            'stoch_d': last['stoch_d'],
            'stoch_trend': stoch_trend,
            'senkou_a': current_senkou_a, # Gunakan nilai yang sudah diperbaiki
            'senkou_b': current_senkou_b  # Gunakan nilai yang sudah diperbaiki
        }
        
        console_message = f"\n=== [{tf.upper()}] BTC/USDT ===\n" \
                          f"Waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n" \
                          f"Price: {last['close']:.2f} ({candle_type}, {price_action_info})\n" \
                          f"Trend (Ichimoku): {trend}\n" \
                          f"EMA {settings['ema1']}/{settings['ema2']}: {ema_cross}\n" \
                          f"RSI: {last['rsi']:.2f}\n" \
                          f"Stochastic K: {last['stoch_k']:.2f}, D: {last['stoch_d']:.2f} ({stoch_trend})\n" \
                          f"Ichimoku Cloud: ISA:{current_senkou_a:.2f}, ISB:{current_senkou_b:.2f}\n" \
                          f"===============================\n"
        logging.info(console_message)

        return analysis_data, last['time']
        
    except ccxt.NetworkError as e:
        logging.error(f"[{tf}] Network error: {e}. Retrying soon...")
        return None, None
    except ccxt.ExchangeError as e:
        if 'Too many requests' in str(e) or 'limit' in str(e).lower():
            logging.warning(f"[{tf}] Rate limit hit: {e}. Waiting longer before next attempt.")
            time.sleep(exchange.rateLimit / 1000 + 5)
        else:
            logging.error(f"[{tf}] Exchange error: {e}. Check symbol or API limits.")
        return None, None
    except pd.errors.EmptyDataError:
        logging.warning(f"[{tf}] Empty data received from exchange. Might be temporary API issue or no data.")
        return None, None
    except Exception as e:
        logging.critical(f"[{tf}] An unexpected critical error occurred: {e}", exc_info=True)
        return None, None

def send_to_discord(message=None, embed=None):
    """Mengirim pesan atau embed ke Discord webhook."""
    if not DISCORD_WEBHOOK_URL: 
        logging.error("Discord Webhook URL not configured. Please set DISCORD_WEBHOOK_URL.")
        return

    payload = {}
    if message:
        payload["content"] = message
    if embed:
        payload["embeds"] = [embed]

    if not payload:
        logging.warning("No content or embed provided for Discord message. Skipping.")
        return

    try:
        response = requests.post(DISCORD_WEBHOOK_URL, json=payload)
        response.raise_for_status()
        logging.info("Message sent to Discord successfully!")
    except requests.exceptions.RequestException as e:
        logging.error(f"Failed to send message to Discord: {e}", exc_info=True)

def send_combined_analysis(all_analysis_data):
    """
    Menggabungkan data analisis dari semua timeframe ke dalam satu embed Discord tunggal.
    """
    if not all_analysis_data:
        logging.info("No analysis data available to send to Discord.")
        return

    embed_fields = []
    main_color = 0x00FF00 # Hijau (Bullish)

    has_bearish = False
    has_sideways = False

    for tf_data in all_analysis_data:
        if tf_data['trend'] == 'BEARISH':
            has_bearish = True
        elif tf_data['trend'] == 'SIDEWAYS':
            has_sideways = True
        
        field_value = (
            f"**Harga:** `{tf_data['close_price']:.2f}` USDT\n"
            f"**Candle:** {tf_data['candle_type']} ({tf_data['price_action']})\n"
            f"**Trend (Ichimoku):** **{tf_data['trend']}**\n"
            f"**EMA:** {tf_data['ema_cross']}\n"
            f"**RSI:** `{tf_data['rsi']:.2f}` | **Stoch:** K:`{tf_data['stoch_k']:.2f}` D:`{tf_data['stoch_d']:.2f}` ({tf_data['stoch_trend']})\n"
            f"**Ichimoku Cloud:** ISA:`{tf_data['senkou_a']:.2f}` | ISB:`{tf_data['senkou_b']:.2f}`"
        )
        embed_fields.append({
            "name": f"📊 {symbol} - {tf_data['tf']}",
            "value": field_value,
            "inline": False
        })
    
    if has_bearish:
        main_color = 0xFF0000 # Merah
    elif has_sideways:
        main_color = 0xFFFF00 # Kuning
    else:
        main_color = 0x00FF00 # Hijau

    embed = {
        "title": f"📈 BTC/USDT - Combined Market Analysis",
        "description": f"Update waktu: {datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}\n\n",
        "color": main_color,
        "fields": embed_fields,
        "footer": {"text": "Data dari Binance via CCXT & pandas_ta"}
    }
    
    send_to_discord(embed=embed)


# --- Main Execution Loop ---
if __name__ == "__main__":
    logging.info("Starting crypto analysis bot for 1h, 4h, 1d. Combined updates sent to Discord when new candles close.")

    last_processed_candle_time = {tf: None for tf in timeframes.keys()}
    
    logging.info("\n--- Performing initial combined analysis for all timeframes ---")
    initial_analysis_results = []
    for tf, settings in timeframes.items():
        analysis_data, processed_time = analyze_tf(tf, settings)
        if analysis_data:
            initial_analysis_results.append(analysis_data)
            last_processed_candle_time[tf] = processed_time
        time.sleep(exchange.rateLimit / 1000 + 1)
    
    if initial_analysis_results:
        send_combined_analysis(initial_analysis_results)
    else:
        logging.warning("No initial analysis data to send for combined message.")

    logging.info("\nInitial analyses complete. Entering continuous monitoring mode.")

    while True:
        current_time = datetime.now()
        
        minutes_to_next_15 = 15 - (current_time.minute % 15)
        if minutes_to_next_15 == 15 and current_time.second == 0:
            minutes_to_next_15 = 0

        next_15m_mark = current_time + timedelta(minutes=minutes_to_next_15)
        next_15m_mark = next_15m_mark.replace(second=0, microsecond=0)

        sleep_duration = (next_15m_mark - current_time).total_seconds()
        sleep_duration += 5 

        logging.info(f"\n[{current_time.strftime('%Y-%m-%d %H:%M:%S WIB')}] Waiting for {int(sleep_duration)} seconds until {next_15m_mark.strftime('%H:%M:%S WIB')}...")
        time.sleep(sleep_duration)

        logging.info(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S WIB')}] Checking for new candle closes...")

        updated_timeframes_data = []
        
        for tf, settings in timeframes.items():
            try:
                latest_ohlcv = exchange.fetch_ohlcv(symbol, tf, limit=1)
                
                if latest_ohlcv:
                    latest_candle_time_ms = latest_ohlcv[0][0]
                    latest_candle_time = pd.to_datetime(latest_candle_time_ms, unit='ms')

                    if last_processed_candle_time[tf] is None or latest_candle_time > last_processed_candle_time[tf]:
                        logging.info(f"New {tf.upper()} candle detected! Analyzing...")
                        analysis_data, processed_time = analyze_tf(tf, settings)
                        if analysis_data:
                            updated_timeframes_data.append(analysis_data)
                            last_processed_candle_time[tf] = processed_time
                    else:
                        logging.info(f"No new {tf.upper()} candle since last check. Last processed: {last_processed_candle_time[tf].strftime('%Y-%m-%d %H:%M:%S')}")
                else:
                    logging.warning(f"Could not fetch latest {tf.upper()} candle to check for updates.")
            except Exception as e:
                logging.error(f"Error checking {tf.upper()} candle update: {e}", exc_info=True)
            
            time.sleep(exchange.rateLimit / 1000 + 1)
        
        if updated_timeframes_data:
            send_combined_analysis(updated_timeframes_data)
        else:
            logging.info("No new candles closed across all timeframes. Skipping Discord update.")
