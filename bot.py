import telebot
import requests
import os
import logging
import time
import threading
import json
from datetime import datetime, timedelta
from telebot import types

# ─── Logging setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

# Separate analytics logger — writes structured JSON lines to analytics.log
analytics_logger = logging.getLogger('analytics')
_ah = logging.FileHandler('analytics.log')
_ah.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
analytics_logger.addHandler(_ah)
analytics_logger.setLevel(logging.INFO)

def log_event(user_id, action, extra=None):
    """Record a user action for analytics purposes."""
    entry = {"user_id": user_id, "action": action}
    if extra:
        entry.update(extra)
    analytics_logger.info(json.dumps(entry))

# ─── Bot setup ────────────────────────────────────────────────────────────────

BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing or invalid.")

bot = telebot.TeleBot(BOT_TOKEN)

# ─── Constants ────────────────────────────────────────────────────────────────

SUPPORTED_CURRENCIES = ['BYN', 'USD', 'EUR', 'GBP', 'PLN', 'JPY', 'CHF']

MENU_RATES   = "Rates"
MENU_CONVERT = "Convert"
MENU_ALERTS  = "Alerts"
MENU_HELP    = "Help"
MENU_INFO    = "Info"
MENU_CANCEL  = "Cancel"

# Appended to any message that shows a rate value, so users aren't misled
# into thinking the official rate equals the cash desk rate.
RATE_DISCLAIMER = (
    "\n_⚠️ Official NBRB rate. "
    "Actual exchange desk rates may differ by 1–5%._"
)

# ─── State ────────────────────────────────────────────────────────────────────

rates_cache_by_base = {}   # keyed by date string or "today"
convert_state       = {}   # keyed by chat_id
alert_setup_state   = {}   # keyed by chat_id

# {chat_id: [{"currency": str, "direction": "above"|"below", "threshold": float}]}
user_alerts = {}

# ─── NBRB API ─────────────────────────────────────────────────────────────────

def get_nbrb_rates(ondate=None):
    """
    Fetch NBRB official rates normalised to BYN per 1 unit of currency.
    Results are cached for 10 minutes per date key.
    """
    cache_key = ondate or "today"
    cached = rates_cache_by_base.get(cache_key)
    if cached and (time.time() - cached["fetched_at"] <= 600):
        return cached["rates"], cached.get("as_of")

    params = {"periodicity": 0}
    if ondate:
        params["ondate"] = ondate

    resp = requests.get(
        "https://api.nbrb.by/exrates/rates", params=params, timeout=10
    )
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, list):
        raise requests.exceptions.RequestException("Unexpected NBRB API response shape.")

    as_of = None
    rates_byn_per_unit: dict[str, float] = {"BYN": 1.0}
    for item in data:
        try:
            abbr     = str(item.get("Cur_Abbreviation", "")).upper()
            scale    = float(item.get("Cur_Scale"))
            official = item.get("Cur_OfficialRate")
            if official is None or scale == 0:
                continue
            byn_per_unit = float(official) / scale
            if abbr:
                rates_byn_per_unit[abbr] = byn_per_unit
            if as_of is None and item.get("Date"):
                as_of = str(item["Date"])[:10]
        except Exception as e:
            logging.warning("Skipping rate item: %s", e)
            continue

    rates_cache_by_base[cache_key] = {
        "rates": rates_byn_per_unit,
        "fetched_at": time.time(),
        "as_of": as_of,
    }
    return rates_byn_per_unit, as_of

# ─── Keyboards ────────────────────────────────────────────────────────────────

def build_main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add(
        types.KeyboardButton(MENU_RATES),
        types.KeyboardButton(MENU_CONVERT),
        types.KeyboardButton(MENU_ALERTS),
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
    buttons = [
        types.InlineKeyboardButton(c, callback_data=f"{prefix}:{c}")
        for c in SUPPORTED_CURRENCIES
        if c != exclude
    ]
    kb.add(*buttons)
    return kb

def build_direction_inline_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📈 Above threshold", callback_data="alert_dir:above"),
        types.InlineKeyboardButton("📉 Below threshold", callback_data="alert_dir:below"),
    )
    return kb

# ─── Alert background checker ─────────────────────────────────────────────────

def check_alerts():
    """
    Runs as a daemon thread.
    Every 5 minutes, fetches current rates and fires any alerts whose
    threshold condition has been met. Fired alerts are removed (one-shot).
    """
    while True:
        time.sleep(300)
        if not user_alerts:
            continue
        try:
            rates_byn, _ = get_nbrb_rates()
        except Exception as e:
            logging.warning("Alert check: failed to fetch rates: %s", e)
            continue

        for chat_id, alerts in list(user_alerts.items()):
            remaining = []
            for alert in alerts:
                currency  = alert["currency"]
                threshold = alert["threshold"]
                direction = alert["direction"]
                current   = rates_byn.get(currency)

                if current is None:
                    remaining.append(alert)
                    continue

                triggered = (
                    (direction == "above" and current >= threshold) or
                    (direction == "below" and current <= threshold)
                )

                if triggered:
                    arrow = "📈" if direction == "above" else "📉"
                    try:
                        bot.send_message(
                            chat_id,
                            f"{arrow} *Alert triggered!*\n"
                            f"`{currency}` is now *{current:.4f} BYN*\n"
                            f"Your threshold: {direction} {threshold:.4f} BYN"
                            f"{RATE_DISCLAIMER}",
                            parse_mode='Markdown',
                            reply_markup=build_main_keyboard(),
                        )
                        log_event(chat_id, "alert_triggered", {
                            "currency": currency,
                            "direction": direction,
                            "threshold": threshold,
                            "current": current,
                        })
                    except Exception as e:
                        logging.warning("Could not send alert to %s: %s", chat_id, e)
                    # Alert fires once and is consumed
                else:
                    remaining.append(alert)

            user_alerts[chat_id] = remaining

_alert_thread = threading.Thread(target=check_alerts, daemon=True)
_alert_thread.start()

# ─── Convert helpers ──────────────────────────────────────────────────────────

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
        bot.reply_to(
            message,
            "⚠️ Rate data is unavailable for one of the selected currencies. "
            "Please try again later.",
        )
        return

    result = amount * rates_byn[from_c] / rates_byn[to_c]
    bot.reply_to(
        message,
        f"💰 {amount:g} {from_c} = *{result:.2f} {to_c}*{RATE_DISCLAIMER}",
        parse_mode='Markdown',
        reply_markup=build_main_keyboard(),
    )

# ─── /start ───────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start(message):
    log_event(message.chat.id, "start")
    text = (
        "👋 *Welcome to the BYN Exchange Rate Bot!*\n\n"
        "Here's what I can do:\n\n"
        "💱 /rates — Official NBRB rates with day-over-day changes\n"
        "🔄 /convert — Convert between any supported currencies\n"
        "🔔 /alerts — Get notified when a currency hits your target\n"
        "ℹ️ /info — About this bot and its data source\n\n"
        "Supported: BYN · USD · EUR · GBP · PLN · JPY · CHF\n\n"
        "_Tap a button below to get started._"
    )
    bot.reply_to(
        message, text, parse_mode='Markdown', reply_markup=build_main_keyboard()
    )

# ─── /rates ───────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['rates'])
def rates(message):
    log_event(message.chat.id, "rates")
    try:
        today         = datetime.now().date()
        yesterday_key = (today - timedelta(days=1)).isoformat()

        r_today, as_of = get_nbrb_rates()
        r_y, _         = get_nbrb_rates(ondate=yesterday_key)
        as_of_key      = as_of or today.isoformat()

        text = f"💱 *Official rates (NBRB)*\n_As of {as_of_key}_\n\n"

        for currency in [c for c in SUPPORTED_CURRENCIES if c != "BYN"]:
            value = r_today.get(currency)
            if value is None:
                continue
            # Rate and diff on one line for easy scanning
            diff_str = ""
            if isinstance(r_y, dict) and currency in r_y:
                diff     = value - r_y[currency]
                sign     = "+" if diff >= 0 else ""
                arrow    = "▲" if diff > 0 else ("▼" if diff < 0 else "●")
                diff_str = f"  {arrow} {sign}{diff:.4f}"
            text += f"`{currency}` {value:.4f} BYN{diff_str}\n"

        text += RATE_DISCLAIMER
        bot.reply_to(message, text, parse_mode='Markdown')

    except requests.exceptions.RequestException as e:
        logging.error("Request failed: %s", e)
        bot.reply_to(
            message,
            "⚠️ Could not reach the NBRB API. Please try again in a few minutes.",
        )
    except Exception as e:
        logging.error("Unexpected error: %s", e)
        bot.reply_to(message, "Something went wrong. Please try again later.")

# ─── /convert ─────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['convert'])
def convert(message):
    log_event(message.chat.id, "convert_start")
    start_convert_flow(message.chat.id)

# ─── /help ────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['help'])
def help(message):
    log_event(message.chat.id, "help")
    text = (
        "📖 *Commands*\n\n"
        "/rates — Official NBRB rates with day-over-day changes\n"
        "/convert — Step-by-step currency conversion\n"
        "/alerts — Set a price alert for any currency\n"
        "/help — This message\n"
        "/info — About this bot\n\n"
        "You can also use the keyboard buttons below."
    )
    bot.reply_to(
        message, text, parse_mode='Markdown', reply_markup=build_main_keyboard()
    )

# ─── /info ────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['info'])
def info(message):
    log_event(message.chat.id, "info")
    text = (
        "ℹ️ *About this Bot*\n\n"
        "Provides official exchange rates and currency conversion "
        "for the Belarusian Ruble (BYN).\n\n"
        "*Data source:* [NBRB ExRates API](https://www.nb-rb.by/apihelp/exrates.htm) — "
        "the National Bank of the Republic of Belarus.\n\n"
        "*Update schedule:* NBRB publishes new rates once per day on business days. "
        "Rates are cached locally for 10 minutes.\n\n"
        "⚠️ *Important:* Official NBRB rates are used for accounting and legal purposes. "
        "Rates at exchange desks and banks may differ by 1–5%."
    )
    bot.reply_to(
        message, text, parse_mode='Markdown', reply_markup=build_main_keyboard()
    )

# ─── /alerts ──────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['alerts'])
def alerts_menu(message):
    log_event(message.chat.id, "alerts_menu")
    show_alerts_menu(message.chat.id)

def show_alerts_menu(chat_id):
    alerts = user_alerts.get(chat_id, [])
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("＋ Add new alert", callback_data="alert_add"))

    if alerts:
        kb.add(
            types.InlineKeyboardButton(
                "🗑 Clear all alerts", callback_data="alert_clear_all"
            )
        )
        lines = ["🔔 *Your active alerts:*\n"]
        for i, a in enumerate(alerts, 1):
            arrow = "📈" if a["direction"] == "above" else "📉"
            lines.append(
                f"{i}. {arrow} {a['currency']} {a['direction']} {a['threshold']:.4f} BYN"
            )
        text = "\n".join(lines)
    else:
        text = (
            "🔔 *Rate Alerts*\n\n"
            "You have no active alerts.\n"
            "Set one and I'll message you the moment a currency hits your target."
        )

    bot.send_message(chat_id, text, parse_mode='Markdown', reply_markup=kb)

def start_alert_setup(chat_id):
    alert_setup_state[chat_id] = {"step": "currency"}
    bot.send_message(
        chat_id,
        "Choose the currency to watch:",
        reply_markup=build_currency_inline_keyboard("alert_cur", exclude="BYN"),
    )

@bot.callback_query_handler(
    func=lambda call: call.data in ["alert_add", "alert_clear_all"]
)
def handle_alert_menu_actions(call):
    chat_id = call.message.chat.id
    if call.data == "alert_add":
        bot.answer_callback_query(call.id)
        start_alert_setup(chat_id)
    elif call.data == "alert_clear_all":
        user_alerts.pop(chat_id, None)
        bot.answer_callback_query(call.id, "All alerts cleared.")
        bot.edit_message_text(
            "✅ All your alerts have been cleared.",
            chat_id=chat_id,
            message_id=call.message.message_id,
        )

@bot.callback_query_handler(
    func=lambda call: (
        call.data.startswith("alert_cur:") or
        call.data.startswith("alert_dir:")
    )
)
def handle_alert_setup_callbacks(call):
    chat_id = call.message.chat.id
    state   = alert_setup_state.get(chat_id, {})

    try:
        if call.data.startswith("alert_cur:"):
            currency = call.data.split(":", 1)[1]
            alert_setup_state[chat_id] = {"step": "direction", "currency": currency}
            bot.answer_callback_query(call.id, f"Currency: {currency}")
            bot.edit_message_text(
                f"Watching: *{currency}*\nAlert me when the rate goes:",
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown',
                reply_markup=build_direction_inline_keyboard(),
            )

        elif call.data.startswith("alert_dir:"):
            direction = call.data.split(":", 1)[1]
            currency  = state.get("currency")
            if not currency:
                bot.answer_callback_query(call.id, "Please start again.")
                return

            alert_setup_state[chat_id] = {
                "step": "threshold",
                "currency": currency,
                "direction": direction,
            }

            # Show current rate as a reference so users can set a sensible threshold
            try:
                rates_byn, _ = get_nbrb_rates()
                current = rates_byn.get(currency)
                hint = f"\nCurrent rate: *{current:.4f} BYN*" if current else ""
            except Exception:
                hint = ""

            arrow_label = "📈 above" if direction == "above" else "📉 below"
            bot.answer_callback_query(call.id)
            bot.edit_message_text(
                f"Alert when *{currency}* goes *{arrow_label}* a threshold."
                f"{hint}\n\nSend the BYN value (e.g. `3.25`):",
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown',
            )
            bot.send_message(
                chat_id, "You can cancel anytime.", reply_markup=build_cancel_keyboard()
            )

    except Exception as e:
        logging.error("Alert setup callback error: %s", e)
        bot.answer_callback_query(call.id, "Could not process selection.")

@bot.message_handler(
    func=lambda m: (
        m.chat.id in alert_setup_state and
        alert_setup_state[m.chat.id].get("step") == "threshold"
    )
)
def handle_alert_threshold_step(message):
    chat_id = message.chat.id

    if message.text == MENU_CANCEL:
        alert_setup_state.pop(chat_id, None)
        bot.reply_to(
            message, "Alert setup cancelled.", reply_markup=build_main_keyboard()
        )
        return

    state = alert_setup_state.get(chat_id, {})
    try:
        threshold = float(message.text.replace(",", "."))
        if threshold <= 0:
            raise ValueError("Threshold must be positive.")
    except ValueError:
        bot.reply_to(
            message,
            "Please enter a valid positive number, e.g. `3.25`",
            parse_mode='Markdown',
            reply_markup=build_cancel_keyboard(),
        )
        return

    currency  = state.get("currency")
    direction = state.get("direction")
    if not currency or not direction:
        alert_setup_state.pop(chat_id, None)
        bot.reply_to(
            message,
            "Alert setup expired. Please tap Alerts again.",
            reply_markup=build_main_keyboard(),
        )
        return

    user_alerts.setdefault(chat_id, []).append(
        {"currency": currency, "direction": direction, "threshold": threshold}
    )
    alert_setup_state.pop(chat_id, None)

    arrow = "📈" if direction == "above" else "📉"
    log_event(chat_id, "alert_set", {
        "currency": currency, "direction": direction, "threshold": threshold
    })
    bot.reply_to(
        message,
        f"✅ *Alert set!*\n"
        f"{arrow} I'll notify you when *{currency}* goes *{direction}* "
        f"*{threshold:.4f} BYN*.\n\n"
        f"_Rates are checked every 5 minutes._",
        parse_mode='Markdown',
        reply_markup=build_main_keyboard(),
    )

# ─── Menu button routing ──────────────────────────────────────────────────────

@bot.message_handler(
    func=lambda m: m.text in [
        MENU_RATES, MENU_CONVERT, MENU_ALERTS, MENU_HELP, MENU_INFO
    ]
)
def handle_menu_buttons(message):
    dispatch = {
        MENU_RATES:   rates,
        MENU_CONVERT: lambda m: start_convert_flow(m.chat.id),
        MENU_ALERTS:  alerts_menu,
        MENU_HELP:    help,
        MENU_INFO:    info,
    }
    dispatch[message.text](message)

# ─── Convert callbacks ────────────────────────────────────────────────────────

@bot.callback_query_handler(
    func=lambda call: (
        call.data.startswith("convert_from:") or
        call.data.startswith("convert_to:")
    )
)
def handle_convert_callbacks(call):
    chat_id = call.message.chat.id
    state   = convert_state.get(chat_id, {})

    try:
        if call.data.startswith("convert_from:"):
            from_c = call.data.split(":", 1)[1]
            convert_state[chat_id] = {"step": "to", "from": from_c}
            bot.answer_callback_query(call.id, f"From: {from_c}")
            bot.edit_message_text(
                f"From: *{from_c}*\nNow choose target currency:",
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown',
                reply_markup=build_currency_inline_keyboard("convert_to", exclude=from_c),
            )

        elif call.data.startswith("convert_to:"):
            to_c   = call.data.split(":", 1)[1]
            from_c = state.get("from")
            if not from_c:
                bot.answer_callback_query(call.id, "Please start again with /convert")
                return
            convert_state[chat_id] = {"step": "amount", "from": from_c, "to": to_c}
            bot.answer_callback_query(call.id, f"To: {to_c}")
            bot.edit_message_text(
                f"Convert *{from_c}* → *{to_c}*\nSend the amount (e.g. `100`):",
                chat_id=chat_id,
                message_id=call.message.message_id,
                parse_mode='Markdown',
            )
            bot.send_message(
                chat_id, "You can cancel anytime.", reply_markup=build_cancel_keyboard()
            )
            log_event(chat_id, "convert_pair_selected", {"from": from_c, "to": to_c})

    except Exception:
        bot.answer_callback_query(call.id, "Could not process selection.")

# ─── Convert amount step ──────────────────────────────────────────────────────

@bot.message_handler(
    func=lambda m: (
        m.chat.id in convert_state and
        convert_state[m.chat.id].get("step") == "amount"
    )
)
def handle_convert_amount_step(message):
    chat_id = message.chat.id

    if message.text == MENU_CANCEL:
        convert_state.pop(chat_id, None)
        bot.reply_to(
            message, "Conversion cancelled.", reply_markup=build_main_keyboard()
        )
        return

    state  = convert_state.get(chat_id, {})
    from_c = state.get("from")
    to_c   = state.get("to")

    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        bot.reply_to(
            message,
            "Enter a valid number, e.g. `100`",
            parse_mode='Markdown',
            reply_markup=build_cancel_keyboard(),
        )
        return

    if not from_c or not to_c:
        convert_state.pop(chat_id, None)
        bot.reply_to(
            message,
            "Conversion flow expired. Please tap Convert again.",
            reply_markup=build_main_keyboard(),
        )
        return

    try:
        log_event(chat_id, "convert_completed", {
            "from": from_c, "to": to_c, "amount": amount
        })
        convert_amount_message(message, amount, from_c, to_c)
    except requests.exceptions.RequestException as e:
        logging.error("Request failed: %s", e)
        bot.reply_to(
            message,
            "⚠️ Could not reach the NBRB API. Please try again in a few minutes.",
            reply_markup=build_main_keyboard(),
        )
    except Exception as e:
        logging.error("Unexpected error: %s", e)
        bot.reply_to(
            message,
            "Something went wrong. Please try again later.",
            reply_markup=build_main_keyboard(),
        )
    finally:
        convert_state.pop(chat_id, None)

# ─── Run ──────────────────────────────────────────────────────────────────────

bot.polling()
