import telebot
import requests
import os
import logging
import time
from datetime import datetime, timedelta

# Set up logging
logging.basicConfig(level=logging.INFO)

# Bot token validation
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing or invalid.")

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Define supported currencies
SUPPORTED_CURRENCIES = ['BYN', 'USD', 'EUR', 'GBP', 'PLN', 'JPY', 'CHF']

# Cache for rates
rates_cache_by_base = {}

# NBRB provides official rates as BYN per Cur_Scale units of currency.
# We normalize to BYN per 1 unit so conversions and diffs are consistent.
def get_nbrb_rates(ondate=None):
    cache_key = ondate or "today"
    cached = rates_cache_by_base.get(cache_key)
    if cached and (time.time() - cached["fetched_at"] <= 600):
        return cached["rates"], cached.get("as_of")

    params = {"periodicity": 0}
    if ondate:
        params["ondate"] = ondate

    resp = requests.get("https://api.nbrb.by/exrates/rates", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise requests.exceptions.RequestException("Unexpected NBRB API response shape.")

    as_of = None
    rates_byn_per_unit: dict[str, float] = {"BYN": 1.0}
    for item in data:
        try:
            abbr = str(item.get("Cur_Abbreviation", "")).upper()
            scale = float(item.get("Cur_Scale"))
            official = item.get("Cur_OfficialRate")
            if official is None or scale == 0:
                continue
            byn_per_unit = float(official) / scale
            if abbr:
                rates_byn_per_unit[abbr] = byn_per_unit
            if as_of is None and item.get("Date"):
                # e.g. "2026-03-27T00:00:00"
                as_of = str(item["Date"])[:10]
        except Exception:
            continue

    rates_cache_by_base[cache_key] = {"rates": rates_byn_per_unit, "fetched_at": time.time(), "as_of": as_of}
    return rates_byn_per_unit, as_of

# Command to start the bot
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Welcome! Use /help to see available commands.")

# Command to get exchange rates to Belarusian Ruble (BYN)
@bot.message_handler(commands=['rates'])
def rates(message):
    try:
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        yesterday_key = yesterday.isoformat()

        r_today, as_of = get_nbrb_rates()
        r_y, _ = get_nbrb_rates(ondate=yesterday_key)
        as_of_key = as_of or today.isoformat()

        text = f"💱 *Official rates (NBRB)*\n_As of {as_of_key}_\n\n"
        for currency in [c for c in SUPPORTED_CURRENCIES if c != "BYN"]:
            value = r_today.get(currency)
            if value is not None:
                text += f"`{currency}`: 1 {currency} = {value:.4f} BYN\n"

        if isinstance(r_y, dict) and r_y:
            text += f"\n📉 *Differences vs {yesterday_key}*\n"
            for currency in [c for c in SUPPORTED_CURRENCIES if c != "BYN"]:
                if currency in r_today and currency in r_y:
                    diff = r_today[currency] - r_y[currency]
                    text += f"`{currency}`: {diff:+.4f} BYN\n"

        bot.reply_to(message, text, parse_mode='Markdown')
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        bot.reply_to(message, "Failed to retrieve exchange rates. Please try again later.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        bot.reply_to(message, "Something went wrong. Please try again later.")

# Command to convert between currencies
@bot.message_handler(commands=['convert'])
def convert(message):
    try:
        _, amount, from_cur, to_cur = message.text.split()
        
        # Validate amount
        try:
            amount = float(amount)
        except ValueError:
            bot.reply_to(message, "Please enter a valid numeric value for the amount.")
            return
        
        # Validate currencies
        if from_cur.upper() not in SUPPORTED_CURRENCIES or to_cur.upper() not in SUPPORTED_CURRENCIES:
            bot.reply_to(message, "Invalid currency code. Please check the supported currencies.")
            return

        rates_byn, _ = get_nbrb_rates()
        from_c = from_cur.upper()
        to_c = to_cur.upper()
        if from_c not in rates_byn or to_c not in rates_byn:
            bot.reply_to(message, "Missing rate for one of the currencies. Please try again later.")
            return

        # amount * (BYN per 1 FROM) gives BYN; divide by (BYN per 1 TO) to get TO.
        result = amount * rates_byn[from_c] / rates_byn[to_c]
        bot.reply_to(
            message,
            f"💰 {amount:g} {from_c} = *{result:.2f} {to_c}*",
            parse_mode='Markdown',
        )
    except ValueError:
        bot.reply_to(message, "Usage: `/convert 100 USD PLN`", parse_mode='Markdown')
    except KeyError:
        bot.reply_to(message, "Invalid currency code. Please check the codes.")
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        bot.reply_to(message, "Failed to retrieve exchange rates. Please try again later.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        bot.reply_to(message, "Something went wrong. Please try again later.")

# Command to show help
@bot.message_handler(commands=['help'])
def help(message):
    text = "📖 *Commands*\n\n/rates — Official rates (NBRB, BYN)\n/convert 100 USD PLN — Convert\n/help — This message"
    bot.reply_to(message, text, parse_mode='Markdown')

# Command to show info about the bot
@bot.message_handler(commands=['info'])
def info(message):
    text = "ℹ️ *About this Bot*\n\nThis bot provides official exchange rates and currency conversion functionality. It uses the [NBRB ExRates API](https://www.nb-rb.by/apihelp/exrates.htm) to fetch official BYN rates. You can also compare today's rates with calendar-yesterday to see changes."
    bot.reply_to(message, text, parse_mode='Markdown')

# Polling to keep the bot running
bot.polling()
