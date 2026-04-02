import telebot
import requests
import os
import logging
import time
from datetime import datetime, timedelta
from telebot import types

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
MENU_RATES = "Rates"
MENU_CONVERT = "Convert"
MENU_HELP = "Help"
MENU_INFO = "Info"
MENU_CANCEL = "Cancel"

# Cache for rates
rates_cache_by_base = {}
convert_state = {}

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

def build_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(MENU_RATES),
        types.KeyboardButton(MENU_CONVERT),
        types.KeyboardButton(MENU_HELP),
        types.KeyboardButton(MENU_INFO),
    )
    return kb

def build_cancel_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    kb.add(types.KeyboardButton(MENU_CANCEL))
    return kb

def build_currency_inline_keyboard(prefix, exclude=None):
    kb = types.InlineKeyboardMarkup(row_width=3)
    buttons = []
    for currency in SUPPORTED_CURRENCIES:
        if currency == exclude:
            continue
        buttons.append(types.InlineKeyboardButton(currency, callback_data=f"{prefix}:{currency}"))
    kb.add(*buttons)
    return kb

def start_convert_flow(chat_id):
    convert_state[chat_id] = {"step": "from"}
    bot.send_message(
        chat_id,
        "Choose source currency:",
        reply_markup=build_currency_inline_keyboard("convert_from"),
    )

def convert_amount_message(message, amount, from_c, to_c):
    rates_byn, _ = get_nbrb_rates()
    if from_c not in rates_byn or to_c not in rates_byn:
        bot.reply_to(message, "Missing rate for one of the currencies. Please try again later.")
        return

    # amount * (BYN per 1 FROM) gives BYN; divide by (BYN per 1 TO) to get TO.
    result = amount * rates_byn[from_c] / rates_byn[to_c]
    bot.reply_to(
        message,
        f"💰 {amount:g} {from_c} = *{result:.2f} {to_c}*",
        parse_mode='Markdown',
        reply_markup=build_main_keyboard(),
    )

# Command to start the bot
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(
        message,
        "👋 Welcome! Use the buttons below or type /help.",
        reply_markup=build_main_keyboard(),
    )

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
    start_convert_flow(message.chat.id)

# Command to show help
@bot.message_handler(commands=['help'])
def help(message):
    text = (
        "📖 *Commands*\n\n"
        "/rates — Official rates (NBRB, BYN)\n"
        "/convert — Step-by-step conversion\n"
        "/help — This message\n\n"
        "You can also use the keyboard buttons."
    )
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=build_main_keyboard())

# Command to show info about the bot
@bot.message_handler(commands=['info'])
def info(message):
    text = "ℹ️ *About this Bot*\n\nThis bot provides official exchange rates and currency conversion functionality. It uses the [NBRB ExRates API](https://www.nb-rb.by/apihelp/exrates.htm) to fetch official BYN rates. You can also compare today's rates with calendar-yesterday to see changes."
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=build_main_keyboard())

@bot.message_handler(func=lambda m: m.text in [MENU_RATES, MENU_CONVERT, MENU_HELP, MENU_INFO])
def handle_menu_buttons(message):
    if message.text == MENU_RATES:
        rates(message)
    elif message.text == MENU_CONVERT:
        start_convert_flow(message.chat.id)
    elif message.text == MENU_HELP:
        help(message)
    elif message.text == MENU_INFO:
        info(message)

@bot.callback_query_handler(func=lambda call: call.data.startswith("convert_from:") or call.data.startswith("convert_to:"))
def handle_convert_callbacks(call):
    chat_id = call.message.chat.id
    state = convert_state.get(chat_id, {})
    try:
        if call.data.startswith("convert_from:"):
            from_c = call.data.split(":", 1)[1]
            convert_state[chat_id] = {"step": "to", "from": from_c}
            bot.answer_callback_query(call.id, f"From: {from_c}")
            bot.edit_message_text(
                f"From currency: {from_c}\nNow choose target currency:",
                chat_id=chat_id,
                message_id=call.message.message_id,
                reply_markup=build_currency_inline_keyboard("convert_to", exclude=from_c),
            )
            return

        if call.data.startswith("convert_to:"):
            to_c = call.data.split(":", 1)[1]
            from_c = state.get("from")
            if not from_c:
                bot.answer_callback_query(call.id, "Please start again with /convert")
                return
            convert_state[chat_id] = {"step": "amount", "from": from_c, "to": to_c}
            bot.answer_callback_query(call.id, f"To: {to_c}")
            bot.edit_message_text(
                f"Convert {from_c} -> {to_c}\nSend amount as a number (example: 100):",
                chat_id=chat_id,
                message_id=call.message.message_id,
            )
            bot.send_message(chat_id, "You can cancel anytime.", reply_markup=build_cancel_keyboard())
    except Exception:
        bot.answer_callback_query(call.id, "Could not process selection")

@bot.message_handler(func=lambda m: m.chat.id in convert_state and convert_state[m.chat.id].get("step") == "amount")
def handle_convert_amount_step(message):
    chat_id = message.chat.id
    if message.text == MENU_CANCEL:
        convert_state.pop(chat_id, None)
        bot.reply_to(message, "Conversion cancelled.", reply_markup=build_main_keyboard())
        return

    state = convert_state.get(chat_id, {})
    from_c = state.get("from")
    to_c = state.get("to")
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        bot.reply_to(message, "Enter a valid number, e.g. `100`", parse_mode='Markdown', reply_markup=build_cancel_keyboard())
        return

    if not from_c or not to_c:
        convert_state.pop(chat_id, None)
        bot.reply_to(message, "Conversion flow expired. Please tap Convert again.", reply_markup=build_main_keyboard())
        return

    try:
        convert_amount_message(message, amount, from_c, to_c)
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        bot.reply_to(message, "Failed to retrieve exchange rates. Please try again later.", reply_markup=build_main_keyboard())
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        bot.reply_to(message, "Something went wrong. Please try again later.", reply_markup=build_main_keyboard())
    finally:
        convert_state.pop(chat_id, None)

# Polling to keep the bot running
bot.polling()
