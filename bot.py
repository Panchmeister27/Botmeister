import telebot
import requests

BOT_TOKEN = "8341522050:AAFklT1jQOlj0c-LrLeBugV5-Rt7lKsW2jo"
bot = telebot.TeleBot(BOT_TOKEN)

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Welcome! Use /help to see available commands.")

@bot.message_handler(commands=['rates'])
def rates(message):
    data = requests.get("https://api.exchangerate-api.com/v4/latest/USD").json()
    r = data['rates']
    text = "💱 *Exchange Rates (USD base)*\n\n"
    for currency in ['EUR', 'GBP', 'PLN', 'JPY', 'CHF']:
        text += f"`{currency}`: {r[currency]}\n"
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['convert'])
def convert(message):
    try:
        _, amount, from_cur, to_cur = message.text.split()
        data = requests.get(f"https://api.exchangerate-api.com/v4/latest/{from_cur.upper()}").json()
        result = float(amount) * data['rates'][to_cur.upper()]
        bot.reply_to(message, f"💰 {amount} {from_cur.upper()} = *{result:.2f} {to_cur.upper()}*", parse_mode='Markdown')
    except:
        bot.reply_to(message, "Usage: `/convert 100 USD PLN`", parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help(message):
    text = "📖 *Commands*\n\n/rates — Exchange rates\n/convert 100 USD PLN — Convert\n/help — This message"
    bot.reply_to(message, text, parse_mode='Markdown')

bot.polling()