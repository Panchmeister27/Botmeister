import telebot
import requests
import os
import logging
import time
from datetime import datetime

# Set up logging
logging.basicConfig(level=logging.INFO)

# Bot token validation
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is missing or invalid.")

# Initialize bot
bot = telebot.TeleBot(BOT_TOKEN)

# Define supported currencies
SUPPORTED_CURRENCIES = ['USD', 'EUR', 'GBP', 'PLN', 'JPY', 'CHF']

# Cache for rates and previous day's rates
rates_cache = None
last_fetch = 0

# Function to get exchange rates with caching
def get_exchange_rates(base_currency):
    global rates_cache, last_fetch
    if time.time() - last_fetch > 600:  # 10 minutes cache timeout
        url = f"https://api.exchangerate-api.com/v4/latest/{base_currency.upper()}"
        data = requests.get(url)
        if data.status_code != 200:
            raise requests.exceptions.RequestException("API request failed.")
        rates_cache = data.json()['rates']
        last_fetch = time.time()
    return rates_cache

# Command to start the bot
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Welcome! Use /help to see available commands.")

# Command to get exchange rates to Belarusian Ruble (BYN)
@bot.message_handler(commands=['rates'])
def rates(message):
    try:
        r = get_exchange_rates('BYN')
        text = "💱 *Exchange Rates (BYN base)*\n\n"
        for currency in SUPPORTED_CURRENCIES[1:]:
            text += f"`{currency}`: {r[currency]}\n"
        bot.reply_to(message, text, parse_mode='Markdown')
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        bot.reply_to(message, "Failed to retrieve exchange rates. Please try again later.")

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
        
        r = get_exchange_rates(from_cur.upper())
        result = amount * r[to_cur.upper()]
        bot.reply_to(message, f"💰 {amount} {from_cur.upper()} = *{result:.2f} {to_cur.upper()}*", parse_mode='Markdown')
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
    text = "📖 *Commands*\n\n"
    text += "/rates — Exchange rates (BYN base)\n"
    text += "/convert 100 USD PLN — Convert between currencies\n"
    text += "/help — This message\n"
    text += "/info — Information about the bot\n"
    text += "/history — See the percentage change in exchange rates from yesterday"
    bot.reply_to(message, text, parse_mode='Markdown')

# Command to show info about the bot
@bot.message_handler(commands=['info'])
def info(message):
    text = "ℹ️ *About this Bot*\n\nThis bot provides exchange rates and currency conversion functionality. It uses the [ExchangeRate-API](https://www.exchangerate-api.com) to fetch real-time data and supports several currencies. You can also compare today's exchange rates with yesterday's rates to see any changes."
    bot.reply_to(message, text, parse_mode='Markdown')

# Command to show the percentage change in exchange rates from yesterday
@bot.message_handler(commands=['history'])
def history(message):
    try:
        today_rates = get_exchange_rates('BYN')
        
        # Fetch yesterday's rates (BYN base) - ExchangeRate API has historical data feature
        yesterday_url = f"https://api.exchangerate-api.com/v4/{(datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')}/BYN"
        yesterday_data = requests.get(yesterday_url)
        if yesterday_data.status_code != 200:
            bot.reply_to(message, "Failed to retrieve yesterday's exchange rates. Please try again later.")
            return

        yesterday_rates = yesterday_data.json()['rates']
        
        # Calculate percentage change
        text = "📉 *Exchange Rate Changes (Compared to Yesterday)*\n\n"
        for currency in SUPPORTED_CURRENCIES[1:]:
            if currency in today_rates and currency in yesterday_rates:
                today_rate = today_rates[currency]
                yesterday_rate = yesterday_rates[currency]
                percent_change = ((today_rate - yesterday_rate) / yesterday_rate) * 100
                text += f"`{currency}`: {percent_change:.2f}%\n"
        
        bot.reply_to(message, text, parse_mode='Markdown')
    except requests.exceptions.RequestException as e:
        logging.error(f"Request failed: {e}")
        bot.reply_to(message, "Failed to retrieve exchange rates. Please try again later.")
    except Exception as e:
        logging.error(f"Unexpected error: {e}")
        bot.reply_to(message, "Something went wrong. Please try again later.")

# Handle non-command messages (users who send something that's not a command)
@bot.message_handler(func=lambda message: not message.text.startswith('/'))
def handle_non_command(message):
    text = "❓ I didn't recognize that message. Here are the available commands:\n\n"
    text += "/rates — Exchange rates (BYN base)\n"
    text += "/convert 100 USD PLN — Convert between currencies\n"
    text += "/help — Show this message\n"
    text += "/info — Information about the bot\n"
    text += "/history — See the percentage change in exchange rates from yesterday"
    bot.reply_to(message, text, parse_mode='Markdown')

# Polling to keep the bot running
bot.polling()
