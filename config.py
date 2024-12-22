import dotenv
import os

dotenv.load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
SHOP_ID = os.getenv("SHOP_ID")
PRODUCT_ID = os.getenv("PRODUCT_ID")
API_KEY = os.getenv("SHOP_API_KEY")
CAPSOLVER_KEY = os.getenv("CAPSOLVER_KEY")
RECAP_SITE_KEY = os.getenv("RECAP_SITE_KEY")
RECAP_SITE_URL = os.getenv("RECAP_SITE_URL")