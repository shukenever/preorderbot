from config import *
from main import logger, processing_queue
from datetime import datetime, timezone
import requests
import json
import string
import random
import asyncio
from telegram.constants import ParseMode
from main import ORDER_FILE, INVOICE_FILE

async def handle_crypto_payment(query, context, payment_method):
    if context.user_data is None or 'hoodpay_id' not in context.user_data:
        await query.edit_message_text("Sorry, I could not process this button click 😕 Please Start again by using /preorder")
        return
    
    hoodpay_id = context.user_data.get('hoodpay_id')
    hoodpay_url = context.user_data.get('hoodpay_url')
    sellpass_id = context.user_data.get('sellpass_id')
    variant_title = context.user_data.get('variant_title')
    price = context.user_data.get('variant_price')
    quantity = context.user_data.get('amount')
    invoice_id = generate_invoice_id()
    user_id = query.from_user.id
    valid_accounts = load_user_data(user_id)
    sellpass_email = valid_accounts[0]['email']
    sellpass_customerid = get_customer_id_by_email(sellpass_email)
    total_price = quantity * price

    status, payment_name, payment_amount, payment_address = select_payment_method(hoodpay_id, payment_method)

    invoice_data = {
        "email": sellpass_email,
        "customer_id": sellpass_customerid,
        "invoice_id": invoice_id,
        "sellpass_id": sellpass_id,
        "hoodpay_id": hoodpay_id,
        "hoodpay_url": hoodpay_url,
        "variant_id": context.user_data['variant_id'],
        "variant_title": variant_title,
        "amount": quantity,
        "total_price": total_price,
        "user_id": query.from_user.id,
        "username": query.from_user.username,
        "payment_method": payment_name,
        "timestamp": datetime.utcnow().isoformat(),
        "status": "AWAITING_PAYMENT"
    }

    if status == 200:
        save_crypto_invoice(invoice_data)
        payment_message = f"""
<strong>Payment Details for Your Order</strong>

<strong>Invoice ID</strong>: <code>{invoice_id}</code>
<strong>Product</strong>: x{quantity} {variant_title}
<strong>Payment Method</strong>: {payment_name}

<strong>Amount to Pay</strong>: <code>{payment_amount}</code>
<strong>Payment Address</strong>: <code>{payment_address}</code>

<b>Instructions:</b>
1. Copy the payment address above.
2. Send exactly <strong>{payment_amount} {payment_name}</strong> to the address.
3. Once the transaction is confirmed, your order will be processed automatically.

⚠️ <i>Note</i>: Ensure that you send the <u>exact amount</u> to avoid any delays or issues.
"""
        await query.edit_message_text(
            text=payment_message, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        asyncio.create_task(check_invoice_status(context, invoice_id, hoodpay_id))
    else:
        logger.error(f"Failed to select payment method: {status} {payment_name}")
        await query.edit_message_text(text=f"Failed to select payment method for {invoice_id}. Please try again or contact support.")


async def check_invoice_status(context, invoice_id, hoodpay_id):
    logger.info(f"Starting status check for Invoice ID: {invoice_id}")
    while True:
        try:
            response = requests.get(f"https://api.hoodpay.io/v1/public/payments/hosted-page/{hoodpay_id}")
            if response.status_code == 200:
                response_data = response.json()
                status_data = response_data.get('data', {})
                
                status = status_data.get("status")
                
                if status == "COMPLETED":
                    logger.info(f"Invoice {invoice_id} COMPLETED. Processing order.")
                    update_invoice_status(invoice_id, "COMPLETED")
                    await process_order(context, invoice_id)
                    return
                elif status in ["EXPIRED", "CANCELLED"]:
                    logger.warning(f"Invoice {invoice_id} marked as {status}. Aborting order.")
                    update_invoice_status(invoice_id, status)
                    return
                else:
                    logger.info(f"Invoice {invoice_id} status: {status}. Rechecking in 1 minute.")
            else:
                logger.error(f"Failed to check status for {invoice_id}. HTTP Status: {response.status_code}")
        except requests.RequestException as e:
            logger.error(f"Error checking invoice status: {e}")

        await asyncio.sleep(60)

async def monitor_pending_invoices(context):
    if not os.path.exists(INVOICE_FILE):
        return

    with open(INVOICE_FILE, 'r') as f:
        invoices = json.load(f)

    for invoice in invoices:
        if invoice['status'] == "AWAITING_PAYMENT":
            logger.info(f"Resuming status check for pending invoice {invoice['invoice_id']}")
            asyncio.create_task(check_invoice_status(context, invoice['invoice_id'], invoice['hoodpay_id']))

async def process_order(context, invoice_id):
    with open(INVOICE_FILE, 'r') as f:
        invoices = json.load(f)

    for invoice in invoices:
        if invoice['invoice_id'] == invoice_id and invoice['status'] == "COMPLETED":
            customer_id = invoice['customer_id']
            total_price = invoice['total_price']

            success, message = remove_balance_to_user(customer_id, total_price)
            if success:
                logger.info(f"Balance successfully deducted for Invoice ID: {invoice_id}")

                order_details = {
                    'user_id': invoice['user_id'],
                    'username': invoice['username'],
                    'variant_id': invoice['variant_id'],
                    'variant_title': invoice['variant_title'],
                    'quantity': invoice['amount'],
                    'payment_method': invoice['payment_method'],
                    'timestamp': invoice['timestamp'],
                    'invoice_id': invoice['invoice_id'],
                    'delivered': False
                }
                save_order_to_file(order_details)
                processing_queue.append(order_details)
                logger.info(f"Order processed successfully for Invoice ID: {invoice_id}")
            else:
                logger.error(f"Failed to remove balance for Invoice ID: {invoice_id}. Message: {message}")
            break

def save_crypto_invoice(invoice_data):
    if not os.path.exists(INVOICE_FILE):
        with open(INVOICE_FILE, 'w') as f:
            json.dump([], f)
    with open(INVOICE_FILE, 'r') as f:
        existing_invoices = json.load(f)

    existing_invoices.append(invoice_data)

    with open(INVOICE_FILE, 'w') as f:
        json.dump(existing_invoices, f, indent=4)
    logger.info(f"Saved new invoice to {INVOICE_FILE} with ID: {invoice_data['invoice_id']}")

def update_invoice_status(invoice_id, new_status):
    if not os.path.exists(INVOICE_FILE):
        return

    with open(INVOICE_FILE, 'r') as f:
        invoices = json.load(f)

    for invoice in invoices:
        if invoice['invoice_id'] == invoice_id:
            invoice['status'] = new_status

    with open(INVOICE_FILE, 'w') as f:
        json.dump(invoices, f, indent=4)
    logger.info(f"Updated status of invoice {invoice_id} to {new_status}")

def get_variants():
    get_url = f"https://dev.sellpass.io/self/{SHOP_ID}/v2/products/{PRODUCT_ID}"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    try:
        response = requests.get(get_url, headers=headers)
        response.raise_for_status()
        product_data = response.json()['data']
    except requests.RequestException as e:
        print(f"Failed to fetch product {PRODUCT_ID}. Error: {e}")
        return []

    if 'product' not in product_data or 'variants' not in product_data['product']:
        print(f"Error: Invalid product data structure for product {PRODUCT_ID}")
        return []

    variants = product_data['product']['variants']

    if not variants:
        print(f"Error: No variants found for product {PRODUCT_ID}")
        return []

    variant_details = []
    for variant in variants:

        variant_info = {
            'id': variant.get('id'),
            'title': variant.get('title', 'Unknown Variant').upper(),
            'price': variant.get('priceDetails', {}).get('amount', 0),
            'stock': variant.get('asSerials', {}).get('stock', 0),
            'min_amount': variant.get('asSerials', {}).get('minAmount', 0),
            'max_amount': variant.get('asSerials', {}).get('maxAmount', 0),
        }
        variant_details.append(variant_info)
    
    return variant_details

def get_customer_id_by_email(email):
    url = f"https://dev.sellpass.io/self/{SHOP_ID}/customers?email={email}"
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            customers = data.get('data', [])
            for customer in customers:
                if customer['customer']['email'] == email:
                    return customer.get("id")
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching customer ID: {e}")
        return None

def get_invoice(invoice_id):
    url = f"https://dev.sellpass.io/self/{SHOP_ID}/invoices/{invoice_id}"
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            invoice_data = data.get('data', {})
            
            hoodpay_info = invoice_data.get('forHoodpayInfo', {})
            external_url = hoodpay_info.get('externalUrl')
            external_payment_id = hoodpay_info.get('externalPaymentId')
            
            return external_url, external_payment_id
        else:
            logger.error(f"Failed to fetch invoice: {response.status_code} {response.text}")
            return None, None
    except requests.RequestException as e:
        logger.error(f"Error fetching invoice data: {e}")
        return None, None

def save_user_data(email, user_id, token, expiry, expiry_raw):
    user_info = {
        "email": email,
        "user_id": user_id,
        "token": token,
        "expiry": expiry.strftime("%Y-%m-%d %H:%M:%S"),
        "expiry_raw": expiry_raw
    }
    with open("buffcreditbot/user_data.txt", "a") as file:
        file.write(json.dumps(user_info) + "\n")

def remove_expired_tokens():
    try:
        while True:
            with open("buffcreditbot/user_data.txt", "r") as file:
                user_data = [json.loads(line) for line in file.readlines()]
            with open("buffcreditbot/user_data.txt", "w") as file:
                for data in user_data:
                    expiry_time = datetime.fromtimestamp(data["expiry_raw"], tz=timezone.utc)
                    if expiry_time > datetime.now(timezone.utc):
                        file.write(json.dumps(data) + "\n")
    except FileNotFoundError:
        pass

def load_user_data(user_id):
    valid_accounts = []
    try:
        with open("buffcreditbot/user_data.txt", "r") as file:
            user_data = [json.loads(line) for line in file.readlines()]
            for data in user_data:
                if data["user_id"] == user_id:
                    expiry_time = datetime.fromtimestamp(data["expiry_raw"], tz=timezone.utc)
                    if expiry_time > datetime.now(timezone.utc):
                        valid_accounts.append(data)
                    else:
                        print(f"Token expired for {data['email']}, removing.")
    except FileNotFoundError:
        pass
    return valid_accounts
  
def select_payment_method(invoice_id, payment_method):
    url = f'https://api.hoodpay.io/v1/public/payments/hosted-page/{invoice_id}/select-payment-method'
    
    headers = {
        'Content-Type': 'application/json'
    }

    if payment_method in ["BITCOIN", "LITECOIN"]:
        post_data = {
            "xPub_Crypto": payment_method,
            "onRamp_Crypto": None
        }
    else:
        post_data = {
            "direct_Crypto": payment_method,
            "onRamp_Crypto": None
        }

    try:
        response = requests.post(url, json=post_data, headers=headers)
        if response.status_code == 200:
            data = response.json()
            invoice_data = data.get('data', {})
            
            payment_amount = invoice_data.get('chargeCryptoAmount')
            payment_name = invoice_data.get('chargeCryptoName')
            payment_address = invoice_data.get('chargeCryptoAddress')
            
            return response.status_code, payment_name, payment_amount, payment_address
        else:
            logger.error(f"Failed to parse payment method: {response.status_code} {response.text}")
            return None, None, None, None
    except requests.RequestException as e:
        logger.error(f"Error parsing payment method: {e}")
        return None, None, None, None

def generate_invoice_id():
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_string = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BuffPal-{timestamp}-{random_string}"

def save_order_to_file(order_details):
    if not os.path.exists(ORDER_FILE):
        with open(ORDER_FILE, 'w') as f:
            json.dump([], f)
    with open(ORDER_FILE, 'r') as f:
        existing_orders = json.load(f)

    existing_orders.append(order_details)

    with open(ORDER_FILE, 'w') as f:
        json.dump(existing_orders, f, indent=4)
    logger.info(f"Order saved successfully for Invoice ID: {order_details['invoice_id']}")

def get_customer_data_by_email(email):
    url = f"https://dev.sellpass.io/self/{SHOP_ID}/customers?email={email.lower()}"
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            customers = data.get('data', [])
            for customer in customers:
                if customer['customer']['email'] == email:
                    return customer
        return None
    except requests.RequestException as e:
        logger.error(f"Error fetching customer info: {e}")
        return None
    
def add_balance_to_user(customer_id, amount):
    url = f'https://dev.sellpass.io/self/{SHOP_ID}/customers/{customer_id}/balance/add'
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {"amount": amount}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return f"Added ${amount} to customer ID {customer_id}.", response.status_code
        else:
            return response.json().get('errors', [response.text])[0], response.status_code
    except requests.RequestException as e:
        logger.error(f"Error adding balance: {e}")
        return str(e), None

def remove_balance_to_user(customer_id, amount):
    url = f'https://dev.sellpass.io/self/{SHOP_ID}/customers/{customer_id}/balance/remove'
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {"amount": amount}
    try:
        response = requests.post(url, json=payload, headers=headers)
        if response.status_code == 200:
            return f"Removed ${amount} to customer ID {customer_id}.", response.status_code
        else:
            return response.json().get('errors', [response.text])[0], response.status_code
    except requests.RequestException as e:
        logger.error(f"Error adding balance: {e}")
        return str(e), None

def remove_balance_by_email(email, amount):
    url = f"https://dev.sellpass.io/self/{SHOP_ID}/customers?email={email}"
    headers = {
        'Authorization': f'Bearer {API_KEY}',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.get(url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            customers = data.get('data', [])
            for customer in customers:
                if customer['customer']['email'] == email:
                    customer_id = customer.get("id")

                    remove_balance_url = f'https://dev.sellpass.io/self/{SHOP_ID}/customers/{customer_id}/balance/remove'
                    payload = {"amount": amount}
                    balance_response = requests.post(remove_balance_url, json=payload, headers=headers)
                    
                    if balance_response.status_code == 200:
                        return f"Removed ${amount} from customer ID {customer_id}.", balance_response.status_code
                    else:
                        return balance_response.json().get('errors', [balance_response.text])[0], balance_response.status_code
            return f"No customer found with email {email}.", 404
        else:
            return f"Error fetching customer ID: {response.status_code}", response.status_code
    except requests.RequestException as e:
        logger.error(f"Error removing balance: {e}")
        return str(e), None
    
def add_balance_to_user_by_email(email, amount):
    customer_id = get_customer_id_by_email(email)
    if customer_id:
        return add_balance_to_user(customer_id, amount)
    return f"Customer with email {email} not found.", None

def remove_balance_to_user_by_email(email, amount):
    customer_id = get_customer_id_by_email(email)
    if customer_id:
        return remove_balance_to_user(customer_id, amount)
    return f"Customer with email {email} not found.", None

def generate_random_code():
    return 'BUFF-' + ''.join(random.choice(
                        string.ascii_letters.upper() + string.ascii_letters.lower() + string.digits) for _ in range(18))