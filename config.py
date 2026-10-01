import os
import sys
from os import getenv
from dotenv import load_dotenv

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Load variables from .env file if it exists
load_dotenv()

try:
    from utils.session_patch import patch_hydrogram_storage
    patch_hydrogram_storage()
except Exception:
    pass

# ==================================================================
# 🔑 API & BOT CREDENTIALS
# ==================================================================
API_ID = int(getenv("API_ID", "0").strip())
API_HASH = getenv("API_HASH", "").strip()
BOT_TOKEN = getenv("BOT_TOKEN", "").strip()
SESSION_STRING = getenv("SESSION_STRING", "").strip()

# Convert empty SESSION_STRING to None for Hydrogram
if not SESSION_STRING:
    SESSION_STRING = None

# Validation
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN not found in .env file!")
if not API_ID or API_ID == 0:
    print("❌ ERROR: API_ID not found or invalid in .env file!")
if not API_HASH:
    print("❌ ERROR: API_HASH not found in .env file!")

# ==================================================================
# 🗄️ DATABASE
# ==================================================================
MONGO_URI = getenv("MONGO_URI", "").strip()

if not MONGO_URI:
    print("❌ WARNING: MONGO_URI not found in .env file!")

# ==================================================================
# 👮‍♂️ ADMIN & CREATOR SETTINGS
# ==================================================================
raw_admins = getenv("ADMINS", "").strip()
ADMINS = [int(x.strip()) for x in raw_admins.split(",") if x.strip()] if raw_admins else []

CREATOR_ID = int(getenv("CREATOR_ID", "0"))
ADMIN_GROUP_ID = int(getenv("ADMIN_GROUP_ID", "0"))
LOG_CHANNEL = int(getenv("LOG_CHANNEL", "0"))

# ==================================================================
# 💰 PAYMENT GATEWAY SETTINGS
# ==================================================================
PAYMENT_UPI_ID = getenv("PAYMENT_UPI_ID", "").strip()
try:
    USDT_RATE = float(getenv("USDT_RATE", "0.0"))
except ValueError:
    USDT_RATE = 0.0

# Crypto Details
BINANCE_ID = getenv("BINANCE_ID", "").strip()
BEP20_ADDRESS = getenv("BEP20_ADDRESS", "").strip()

# Fampay / Email Automation (Optional)
FAMPAY_EMAIL = getenv("FAMPAY_EMAIL", "").strip()
FAMPAY_APP_PASSWORD = getenv("FAMPAY_APP_PASSWORD", "").strip()
FAMPAY_API_KEY = getenv("FAMPAY_API_KEY", "").strip()

# ==================================================================
# 📢 FORCE SUBSCRIBE SETTINGS
# ==================================================================
DEFAULT_FSUB_ID = int(getenv("DEFAULT_FSUB_ID", "0"))
DEFAULT_FSUB_LINK = getenv("DEFAULT_FSUB_LINK", "").strip()

# ==================================================================
# 🎨 UI & MISC
# ==================================================================
STATIC_2FA_PASSWORD = getenv("STATIC_2FA_PASSWORD", "2281")
DIVIDER = getenv("DIVIDER", "━━━━━━━━━━━━━━━━━━━━━━")

# ==================================================================
# 🐛 DEBUG INFO (Optional)
# ==================================================================
if __name__ == "__main__":
    print("\n📋 Configuration Loaded:")
    print(f"✅ API_ID: {API_ID}")
    print(f"✅ API_HASH: {API_HASH[:10]}...")
    print(f"✅ BOT_TOKEN: {BOT_TOKEN[:20]}...")
    print(f"✅ SESSION_STRING: {'Set' if SESSION_STRING else 'Not Set (Will use file session)'}")
    print(f"✅ MONGO_URI: {'Configured' if MONGO_URI else 'Not configured'}")
    print(f"✅ Admin Count: {len(ADMINS)}")