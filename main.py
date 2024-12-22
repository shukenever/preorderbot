from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ConversationHandler, ContextTypes, InvalidCallbackData, CallbackContext
from config import *
from telegram.constants import ParseMode
from telegram.error import NetworkError, TelegramError
from captcha_solver import solve_captcha
from datetime import datetime, timezone
from collections import deque
import threading
from func import *
import asyncio
import logging
import aiohttp
import re
import json
import requests
import jwt

processing_queue = deque()
AUTHORIZED_USER_IDS = [5847781069, 5211092406]
MAX_OTP_ATTEMPTS = 3


ORDER_FILE = "preorders.json"
INVOICE_FILE = "crypto_invoices.json"

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[
        logging.FileHandler('bot.log', mode='a'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def log_message(update: Update, context: CallbackContext) -> None:
    user = update.message.from_user
    logger.info("Message from (%s) (@%s): %s", user.id, user.username, update.message.text)

def log_command(update: Update, context: CallbackContext, command_name: str) -> None:
    user = update.message.from_user
    logger.info("Command from (%s) (@%s): %s", user.id, user.username, update.message.text)

async def start(update: Update, context):
    log_command(update, context, 'start')
    welcome_message = """
Welcome to Buffed Credit Bot! Here are the available commands:
1. /login - Login with your Sellpass email. You'll receive a 6-digit code.
2. /logout - Log out of your current session.
3. /status - Check your account logged in status.
4. /preorder - Preorder an item from the shop.
5. /queue - Check your position in the delivery queue.
    """

    welcome_message2= """
Welcome to Buffed Credit Bot! Here are the available commands:
1. /login - Login with your Sellpass email. You'll receive a 6-digit code.
2. /logout - Log out of your current session.
3. /status - Check your account logged in status.
4. /preorder - Preorder an item from the shop.
5. /reset <telegram_user_id> - Admin command to reset a user's login.
6. /queue - Check your position in the delivery queue.
7. /fullqueue - View the full delivery queue.
    """
    
    if update.message.from_user.id in AUTHORIZED_USER_IDS:
        await update.message.reply_text(welcome_message2)
    else:
        await update.message.reply_text(welcome_message)

async def login(update: Update, context):
    user_id = update.message.from_user.id
    valid_accounts = load_user_data(user_id)

    if valid_accounts:
        await update.message.reply_text("You are already logged in as " + valid_accounts[0]['email'] + ". Please /logout first to log in again.")
        return
    
    context.user_data['state'] = 'waiting_for_email'
    context.user_data['otp_attempts'] = 0
    await update.message.reply_text(text="Please enter your email:")

async def handle_captcha_solution(update: Update, context, email):
    recaptcha_token = await solve_captcha()

    if recaptcha_token:
        otp_request_status = send_otp_request(email, recaptcha_token)

        if otp_request_status:
            await update.message.reply_text("OTP sent to your email. Please enter the 6-digit OTP:")
            context.user_data['state'] = 'waiting_for_otp'
        else:
            await update.message.reply_text("Failed to send OTP. Please try again.")
            context.user_data['state'] = 'waiting_for_email'
    else:
        await update.message.reply_text("Captcha solving failed.")
        context.user_data['state'] = 'waiting_for_email'

def send_otp_request(email, recaptcha_token):
    postdata = {
        "email": email,
        "recaptcha": recaptcha_token,
        "referralCode": None
    }
    url = f"https://api.sellpass.io/{SHOP_ID}/customers/auth/otp/request/"

    try:
        response = requests.post(url, json=postdata)
        if response.status_code == 200:
            return True
        else:
            print(f"OTP Request failed: {response.text}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Error in OTP request: {e}")
        return False

def verify_otp(email, otp, recaptcha_token, update):
    postdata = {
        "email": email,
        "otp": otp,
        "recaptcha": recaptcha_token,
        "referralCode": None,
        "tsId": None
    }
    url = f"https://api.sellpass.io/{SHOP_ID}/customers/auth/otp/login/"

    try:
        response = requests.post(url, json=postdata)
        if response.status_code == 200:
            data = response.json()
            token = data["data"]
            expiry = jwt.decode(token, options={"verify_signature": False})["exp"]
            expiry_date = datetime.fromtimestamp(expiry)
            expiry_time = datetime.fromtimestamp(expiry, tz=timezone.utc)

            save_user_data(email, update.effective_user.id, token, expiry_date, expiry)

            return True, expiry_time
        else:
            print(f"OTP Verification failed: {response.text}")
            return False, None
    except requests.exceptions.RequestException as e:
        print(f"Error in OTP verification: {e}")
        return False, None

async def status(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'status')
    user_id = update.message.from_user.id
    
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return
    
    await update.message.reply_text(f"Logged in as: {valid_accounts[0]['email']}, your session expires in {valid_accounts[0]['expiry']}")

async def logout(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'logout')
    user_id = update.message.from_user.id
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return
    
    try:
        with open("buffcreditbot/user_data.txt", "r") as file:
            user_data = [json.loads(line) for line in file.readlines()]
        
        with open("buffcreditbot/user_data.txt", "w") as file:
            for data in user_data:
                if data["user_id"] != update.effective_user.id:
                    file.write(json.dumps(data) + "\n")

        await update.effective_message.reply_text("Logged out successfully!")
    except FileNotFoundError:
        pass

async def cancel(update: Update, context):
    log_command(update, context, 'cancel')
    context.user_data.clear()
    await update.effective_message.reply_text("Cancelled!")

async def reset(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'reset')
    
    user_id = update.message.from_user.id
    
    if user_id in AUTHORIZED_USER_IDS:
        args = context.args
        if len(args) != 1:
            await update.message.reply_text("Usage: /reset <telegram_user_id>")
            return

        target_user_id = args[0]
        valid_accounts = load_user_data(target_user_id)

        if not valid_accounts:
            await update.message.reply_text("No active session found for user {target_user_id}.")
            return
        
        try:
            with open("buffcreditbot/user_data.txt", "r") as file:
                user_data = [json.loads(line) for line in file.readlines()]
            
            with open("buffcreditbot/user_data.txt", "w") as file:
                for data in user_data:
                    if data["user_id"] != update.effective_user.id:
                        file.write(json.dumps(data) + "\n")

            await update.message.reply_text(f"User {target_user_id}'s email login has been reset.")
            logger.info(f"Admin {user_id} reset login for user {target_user_id}")
        except FileNotFoundError:
            pass

async def preorder(update: Update, context: CallbackContext) -> None:
    log_command(update, context, 'preorder')
    user_id = update.message.from_user.id
   
    valid_accounts = load_user_data(user_id)

    if not valid_accounts:
        await update.message.reply_text("You're not logged in. Please use /login to log in.")
        return

    elif len(valid_accounts) != 1:
        await update.message.reply_text("Multiple logged in accounts found. Please use /logout and log in again.")
        return
    
    variants = get_variants()
    
    keyboard = [
        [InlineKeyboardButton(
            text=f"💰 {variant['title']} - ${variant['price']}", 
            callback_data=f"preordero,{variant['id']},{variant['title']},{variant['stock']},{variant['price']},{variant['min_amount']},{variant['max_amount']}"
        )]
        for variant in variants
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Select a variant to preorder:", reply_markup=reply_markup)

async def button(update: Update, context):
    query = update.callback_query
    await query.answer()

    if query.data.startswith("preordero,"):
        variant_data = query.data.split(',')
        variant_id = variant_data[1]
        variant_title = variant_data[2]
        stock = int(variant_data[3])
        price = float(variant_data[4])
        minamount = variant_data[5]
        maxamount = variant_data[6]
        
        
        context.user_data['variant_id'] = variant_id
        context.user_data['variant_title'] = variant_title
        context.user_data['variant_stock'] = stock
        context.user_data['variant_price'] = price
        context.user_data['variant_minAmount'] = minamount
        context.user_data['variant_maxAmount'] = maxamount
        
        
        context.user_data['state'] = 'waiting_for_amount'
        await update.effective_chat.send_message(f"You selected: {variant_title}.\n\nPlease enter the amount you would like to preorder. (Min: {minamount})")
    elif query.data.startswith("coin_"):
        crypto_map = {
        'coin_LTC': 'LITECOIN',
        'coin_USDT_TRX': 'TRX_TETHER',
        'coin_USDT_ETH': 'ETH_TETHER',
        'coin_BTC': 'BITCOIN',
        'coin_ETH': 'ETHEREUM',
        'coin_TRX': 'TRON'
    }
        selected_payment_method = crypto_map.get(query.data)
        if selected_payment_method:
            await handle_crypto_payment(query, context, selected_payment_method)
        else:
            await query.edit_message_text(text="Unsupported cryptocurrency selected.")
    elif query.data == 'crypto':
        if context.user_data is None or 'amount' not in context.user_data:
            await query.edit_message_text("Sorry, I could not process this button click 😕 Please Start again by using /preorder")
            return
        
        valid_accounts = load_user_data(update.effective_user.id)

        if not valid_accounts:
            await update.message.reply_text("You're not logged in. Please use /login to log in.")
            return
        
        api_token = valid_accounts[0]['token']
        quantity = context.user_data['amount']
        price = context.user_data['variant_price']

        total_price = float(quantity * price)

        headers = {
            'Authorization': f'Bearer {api_token}',
            'Content-Type': 'application/json'
        }

        postdata = {
            "amount": str(total_price),
            "gateway": 10,
            "tsId": None
        }
        try:
            response = requests.post(f'https://api.sellpass.io/{SHOP_ID}/customers/dashboard/balance/topup', json=postdata, headers=headers)
            if response.status_code == 200:
                response_data = response.json()
                invoice_id = response_data.get('data', {})
                
                if invoice_id:
                    hoodpay_url, hoodpay_id = get_invoice(invoice_id)
                    
                    context.user_data['sellpass_id'] = invoice_id
                    context.user_data['hoodpay_url'] = hoodpay_url
                    context.user_data['hoodpay_id'] = hoodpay_id
                    context.user_data['total_price'] = total_price
                    keyboard = [
                        [InlineKeyboardButton("💰 LTC", callback_data='coin_LTC')],
                        [InlineKeyboardButton("💰 USDT (TRON)", callback_data='coin_USDT_TRX')],
                        [InlineKeyboardButton("💰 USDT (Ethereum)", callback_data='coin_USDT_ETH')],
                        [InlineKeyboardButton("💰 BTC", callback_data='coin_BTC')],
                        [InlineKeyboardButton("💰 ETH", callback_data='coin_ETH')],
                        [InlineKeyboardButton("💰 TRX", callback_data='coin_TRX')]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.callback_query.edit_message_text("Select a cryptocurrency for your payment:", reply_markup=reply_markup)
                else:
                    logger.error(f"Failed to create balance invoice: {response.status_code} {response.text}")
                    await query.edit_message_text("Failed to create top-up invoice. Please try again.")
            else:
                logger.error(f"Failed to create balance invoice: {response.status_code} {response.text}")
                await query.edit_message_text("Failed to process the top-up request. Please try again.")
        except requests.RequestException as e:
            logger.error(f"Error while creating balance invoice: {e}")
            await query.edit_message_text("An unexpected error occurred. Please try again.")
    elif query.data == 'balance':
        if context.user_data is None or 'amount' not in context.user_data:
            await query.edit_message_text("Sorry, I could not process this button click 😕 Please Start again by using /preorder")
            return
        quantity = context.user_data['amount']
        price = context.user_data['variant_price']
        variant_title = context.user_data['variant_title']
        variant_id = context.user_data['variant_id']
        
        valid_accounts = load_user_data(update.effective_user.id)

        if not valid_accounts:
            await update.message.reply_text("You're not logged in. Please use /login to log in.")
            return

        email = valid_accounts[0]['email']
        user_balance = get_customer_data_by_email(email)
        balances = user_balance.get("customerForShopAccount", {}).get("balances", [{}])[0]
        real_balance = balances.get("realBalance", 0)
        manual_balance = balances.get("manualBalance", 0)
        balance = float(real_balance + manual_balance)

        total_price = quantity * price

        if balance < total_price:
            await query.edit_message_text(f"Insufficient balance. Your balance is ${balance}. Please try again.")
            context.user_data.clear()
            return
        
        _ , status = remove_balance_by_email(email, total_price)

        if status == 200:
            invoice_id = generate_invoice_id()
            user = update.effective_user
            order_data = {
                "user_id": user.id,
                "username": str(user.username),
                "variant_id": variant_id,
                "variant_title": variant_title,
                "quantity": quantity,
                "payment_method": 'balance',
                "timestamp": datetime.now().isoformat(),
                "invoice_id": invoice_id,
                "delivered": False
            }

            save_order_to_file(order_data)
            processing_queue.append(order_data)
            await query.edit_message_text(f"Your order for x{quantity} {variant_title} has been saved.\nInvoice ID: {invoice_id}\nIt will be delivered when the stock is available!")
        else:
            await query.edit_message_text(f"Failed to process the payment. Please try again.")
            context.user_data.clear()

async def message_handler(update: Update, context):
    state = context.user_data.get('state')
    
    if state == 'waiting_for_amount':
        amount = int(update.message.text)
        context.user_data['state'] = None
        variant_title = context.user_data['variant_title']
        minamount = context.user_data['variant_minAmount']
        maxamount = context.user_data['variant_maxAmount']
        
        if amount > int(maxamount) or amount < int(minamount):
            await update.message.reply_text(f"Invalid amount. Please enter an amount between {minamount} and {maxamount}.")
            context.user_data['state'] = 'waiting_for_amount'
            return
        
        context.user_data['amount'] = amount
        
        valid_accounts = load_user_data(update.effective_user.id)

        if not valid_accounts:
            await update.message.reply_text("You're not logged in. Please use /login to log in.")
            return

        email = valid_accounts[0]['email']
        user_balance = get_customer_data_by_email(email.lower())
        balances = user_balance.get("customerForShopAccount", {}).get("balances", [{}])[0]
        real_balance = balances.get("realBalance", 0)
        manual_balance = balances.get("manualBalance", 0)
        balance = float(real_balance + manual_balance)
        
        keyboard = [
        [
            InlineKeyboardButton("Pay with Balance 💳", callback_data='balance'),
            InlineKeyboardButton("Pay with Crypto 💰", callback_data='crypto')
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"You selected: {variant_title}.\n\nBalance: ${balance}\nHow would you like to pay?", reply_markup=reply_markup)
    elif state == 'waiting_for_email':
        log_command(update, context, 'login')
        email = update.message.text
        if re.match(r"[^@]+@[^@]+\.[^@]+", email):
            await update.message.reply_text("Email validated. Solving captcha and requesting OTP...")

            context.user_data['state'] = 'waiting_for_captcha'
            context.user_data['email'] = email

            captcha_task = asyncio.create_task(handle_captcha_solution(update, context, email))
        else:
            await update.message.reply_text("Invalid email. Please try again.")
    elif state == 'waiting_for_otp':
        log_command(update, context, 'otp')
        otp = update.message.text

        if context.user_data['otp_attempts'] > MAX_OTP_ATTEMPTS:
            await update.message.reply_text(f"Maximum OTP attempts exceeded. Please try again.")
            context.user_data['state'] = None
            return

        if len(otp) == 6 and otp.isdigit():
            context.user_data['otp_attempts'] += 1
            await update.message.reply_text("Validating OTP...")

            email = context.user_data.get('email')
            recaptcha_token = await solve_captcha()

            if recaptcha_token:
                otp_verification_status, expiry_time = verify_otp(email, otp, recaptcha_token, update)

                if otp_verification_status:
                    context.user_data['state'] = None
                    await update.message.reply_text(
                        f"You have successfully logged in! Your session expires at {expiry_time} UTC.")
                else:
                    await update.message.reply_text(f"Invalid OTP. {MAX_OTP_ATTEMPTS - context.user_data['otp_attempts']} attempts left.")
            else:
                await update.message.reply_text("Captcha solving failed for OTP verification.")
        else:
            await update.message.reply_text(f"Invalid OTP format. {MAX_OTP_ATTEMPTS - context.user_data['otp_attempts']} attempts left.")

async def handle_invalid_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    await update.effective_message.edit_text(
        "Sorry, I could not process this button click 😕 Please send /start to get a new keyboard."
    )

async def my_queue_position(update: Update, context: CallbackContext):
    log_command(update, context, 'queue')
    user_id = update.effective_user.id

    with open(ORDER_FILE, 'r') as file:
        try:
            orders = json.load(file)
        except json.JSONDecodeError:
            orders = []

    processing_queue.clear()
    for order in orders:
        if not order['delivered']:
            processing_queue.append(order)

    position = 1
    for order in processing_queue:
        if order['user_id'] == user_id:
            await update.message.reply_text(f"🔢 You are currently number {position} in the delivery queue.")
            return
        position += 1

    await update.message.reply_text("You are not in the delivery queue or your order has already been processed.")

async def view_full_queue(update: Update, context: CallbackContext):
    log_command(update, context, 'fullqueue')
    user_id = update.message.from_user.id
    if user_id in AUTHORIZED_USER_IDS:
        if not processing_queue:
            await update.message.reply_text("The queue is currently empty.")
            return

        queue_list = "\n".join([f"{index + 1}. {order['username']} (ID: {order['invoice_id']})" for index, order in enumerate(processing_queue)])
        await update.message.reply_text(f"📋 Current Queue:\n\n{queue_list}")

async def delete_message_after_delay(context, stock_message):
    await asyncio.sleep(5)
    await context.bot.delete_message(stock_message.chat.id, stock_message.message_id)

def schedule_startup_jobs(app):
    job_queue = app.job_queue
    job_queue.run_once(monitor_pending_invoices, when=0)
    print("Scheduled pending invoice monitoring.")

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('help', start))
    app.add_handler(CommandHandler('login', login))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('logout', logout))
    app.add_handler(CommandHandler('clear', cancel))
    app.add_handler(CommandHandler('cancel', cancel))
    app.add_handler(CommandHandler('reset', reset))
    app.add_handler(CommandHandler('preorder', preorder))
    app.add_handler(CommandHandler('queue', my_queue_position))
    app.add_handler(CommandHandler('fullqueue', view_full_queue))
    app.add_handler(CallbackQueryHandler(handle_invalid_button, pattern=InvalidCallbackData))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))


    try:
        schedule_startup_jobs(app)
        app.run_polling()
        remove_expired_tokens()
        print('Bot is now Online!')
    except NetworkError:
        print("Network error occurred. Retrying...")
    except TelegramError as e:
        print(f"Telegram error occurred: {e}")

if __name__ == '__main__':
    main()