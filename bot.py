import telebot
import requests
import os
import logging
import time

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

# Cache for rates
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

# Command to get exchange rates
@bot.message_handler(commands=['rates'])
def rates(message):
    try:
        r = get_exchange_rates('USD')
        text = "💱 *Exchange Rates (USD base)*\n\n"
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
    text = "📖 *Commands*\n\n/rates — Exchange rates\n/convert 100 USD PLN — Convert\n/help — This message"
    bot.reply_to(message, text, parse_mode='Markdown')

# Polling to keep the bot running
bot.polling()
