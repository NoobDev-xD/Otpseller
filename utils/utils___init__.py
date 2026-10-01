import math
try:
    import pycountry  # type: ignore
except ImportError:
    pycountry = None
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import USDT_RATE
from utils.session_patch import (
    normalize_session_string,
    normalize_session_bytes,
    extract_from_sqlite_bytes,
    session_tuple_to_sqlite_bytes,
    patch_hydrogram_storage
)

# Apply patch automatically
patch_hydrogram_storage()

# ==================================================================
# 💱 CURRENCY & AESTHETIC HELPERS
# ==================================================================


def format_price(amount):
    """Safely formats price, handling Strings like 'FREE'."""
    try:
        if isinstance(amount, str):
            return amount
            
        if amount is None or amount <= 0:
            return "₹0.00"
    
        # Calculation logic
        rate = float(USDT_RATE) if USDT_RATE and float(USDT_RATE) > 0 else 100
        amount_usdt = round(amount / rate, 2)
        return f"₹{amount:,.2f} | ${amount_usdt:,}"
    except Exception:
        return f"₹{amount}"

def get_divider():
    """Returns aesthetic line for UI consistency."""
    return "━━━━━━━━━━━━━━━━━━━━━━"

# ==================================================================
# 🧠 SMART COUNTRY & STRING UTILS
# ==================================================================

def get_country_info(query):
    """
    Smart Detection: 'india' -> {'name': 'India', 'flag': '🇮🇳', 'code': 'IN'}
    """
    if pycountry:
        try:
            matches = pycountry.countries.search_fuzzy(query)
            if matches:
                country = matches[0]
                flag_offset = 127397
                flag = "".join([chr(ord(c) + flag_offset) for c in country.alpha_2])
                return {
                    "name": country.name,
                    "flag": flag,
                    "code": country.alpha_2
                }
        except:
            pass
    return {"name": str(query).title(), "flag": "🏳️", "code": None}

def mask_text(text, visible_start=4, visible_end=2):
    """
    Masks sensitive data: +9198******10
    """
    text_str = str(text)
    if not text_str or len(text_str) <= (visible_start + visible_end):
        return "****"
    
    mask_len = len(text_str) - (visible_start + visible_end)
    return f"{text_str[:visible_start]}{'*' * mask_len}{text_str[-visible_end:]}"

# ==================================================================
# 🔢 ADVANCED PAGINATION ENGINE
# ==================================================================

def get_pagination_keyboard(current_page, total_count, data_list, callback_prefix, row_width=1, items_per_page=100):
    """
    Robust Paginator that handles 'text', 'name', and direct 'callback_data'.
    """
    # 1. Zero Handling
    if total_count == 0:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Out of Stock", callback_data="ignore")],
            [InlineKeyboardButton("🔙 Back", callback_data="home")]
        ])

    # 2. Total Pages Logic
    total_pages = math.ceil(total_count / items_per_page)
    
    # 3. Slicing
    start_idx = (current_page - 1) * items_per_page
    end_idx = start_idx + items_per_page
    current_items = data_list[start_idx:end_idx]
    
    keyboard = []
    
    # 4. Item Buttons
    row = []
    for item in current_items:
        # Multi-key detection
        name = item.get('text') or item.get('name') or "Unknown Item"
        
        if 'price' in item and 'text' not in item:
            btn_text = f"{name} • {format_price(item['price'])}"
        else:
            btn_text = name
            
        # callback_data 
        cb_data = item.get('callback_data') or f"{callback_prefix}_{item.get('id')}_{current_page}"
        
        row.append(InlineKeyboardButton(btn_text, callback_data=cb_data))
        
        if len(row) == row_width:
            keyboard.append(row)
            row = []
    
    if row:
        keyboard.append(row)

    # 5. Navigation Row
    if total_pages > 1:
        nav_buttons = []
        # Previous
        if current_page > 1:
            
            nav_buttons.append(InlineKeyboardButton("⬅️", callback_data=f"{callback_prefix}_{current_page-1}"))
        else:
            nav_buttons.append(InlineKeyboardButton("⏺", callback_data="ignore"))

        # Counter
        nav_buttons.append(InlineKeyboardButton(f"📄 {current_page}/{total_pages}", callback_data="ignore"))
        
        # Next
        if current_page < total_pages:
            
            nav_buttons.append(InlineKeyboardButton("➡️", callback_data=f"{callback_prefix}_{current_page+1}"))
        else:
            nav_buttons.append(InlineKeyboardButton("⏺", callback_data="ignore"))
        
        keyboard.append(nav_buttons)


    # 6. Global Back Button
    keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="home")])
    
    return InlineKeyboardMarkup(keyboard)

# ==================================================================
# 💳 PAYMENT & SECURITY
# ==================================================================

def get_upi_qr(amount, upi_id, note="StoreDeposit"):
    """Generates UPI QR Code URL."""
    if amount < 1: return None
    # Standard UPI Deep Link
    upi_link = f"upi://pay?pa={upi_id}&pn=Store&am={amount}&tn={note}&cu=INR"
    return f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&margin=10&data={upi_link}"

def get_readable_time(seconds):
    """Seconds to readable format: 2d 5h"""
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d > 0: return f"{d}d {h}h"
    if h > 0: return f"{h}h {m}m"
    return f"{m}m {s}s"
