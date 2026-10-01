import datetime
import os
import re
import json
import base64
import logging
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from hydrogram.errors import MessageNotModified
from config import STATIC_2FA_PASSWORD, LOG_CHANNEL, API_ID, API_HASH

logger = logging.getLogger(__name__)
from database import (
    get_unique_countries, get_buckets_by_country, 
    buy_item_atomic, get_user, get_product_details, get_order,
    get_country_stats, get_unique_buckets, get_stock_count,
    buy_bucket_atomic, get_live_usdt_rate
)
from utils import format_price, get_pagination_keyboard, get_divider

# Helper for Small Caps 
def small_caps(text):
    trans = str.maketrans("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ")
    return text.lower().translate(trans)

# ==================================================================
# 📂 STEP 1: COUNTRY SELECTION (The First Layer)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^(cat|page_cat)_(accounts|sessions)"))
async def cat_router(c, cb):
    try:
        await show_category_list(c, cb)
    except Exception as e:
        print(f"Cat Pagination Error: {e}")
        await cb.answer("❌ Error loading page!", show_alert=True)

async def show_category_list(c, message_or_callback, category=None):
    is_cb = isinstance(message_or_callback, CallbackQuery)
    msg = message_or_callback.message if is_cb else message_or_callback
    
    # EXTRACTION
    page = 1
    if not category:
        category = "accounts"
        if is_cb:
            data = message_or_callback.data
            if data.startswith("page_cat_"):
                cat_part, page_str = data.replace("page_cat_", "").rsplit("_", 1)
                category = cat_part
                page = int(page_str)
            elif data.startswith("cat_"):
                category = data.replace("cat_", "")
        elif hasattr(message_or_callback, "data") and getattr(message_or_callback, "data"):
            data = message_or_callback.data
            if data.startswith("cat_"):
                category = data.replace("cat_", "")

    item_type = "session" if category == "sessions" else "account"
    # 🔥 CHANGE: Fetch specific buckets instead of just countries
    buckets = await get_unique_buckets(item_type)
    
    if not buckets:
        text = f"<b>🚫 OUT OF STOCK</b>\n\nNo {item_type}s available right now."
        if is_cb:
            return await message_or_callback.answer("Stock Empty!", show_alert=True)
        return await msg.reply_text(text, parse_mode=enums.ParseMode.HTML)

    usdt_rate = await get_live_usdt_rate()
    items_list = []
    list_text = ""
    for item in buckets:
        # bucket structure: {"_id": {"country":..., "price":..., "year":..., "flag":..., "type":...}, "count":..., "sample_id":...}
        meta = item["_id"]
        country = meta.get("country", "Unknown")
        flag = meta.get("flag")
        if not flag or flag == "🏳️":
            from utils import get_country_info
            c_info = get_country_info(country)
            flag = c_info.get("flag") or "🏳️"
        year = meta.get("year", "")
        price = meta.get("price", 0)
        count = item.get("count", 0)
        sample_id = item.get("sample_id")

        price_usdt = round(price / usdt_rate, 2)
        # Format Button Text: 🇮🇳 India 2024 - ₹20 ($0.22)
        btn_text = f"{flag} {country}"
        if year and year != "Fresh":
            btn_text += f" {year}"
        btn_text += f" - ₹{price} (${price_usdt})"

        list_text += f"• {flag} <b>{country} {year}:</b> {count} items\n"

        # 🔥 Robust Callback: pb|cat|country|year|price
        cb_data = f"pb|{category}|{country}|{year}|{price}"
        items_list.append({
            "text": btn_text,
            "callback_data": cb_data
        })

    kb = get_pagination_keyboard(
        current_page=page, 
        total_count=len(items_list),
        data_list=items_list,
        callback_prefix=f"page_cat_{category}",
        row_width=2 # Two buttons per row for better alignment
    )
    
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back to Menu", callback_data="home")])

    header_text = (
        f"<blockquote><b>🛒 SELECT PRODUCT ({category.upper()})\n"
        f"{get_divider()}\n"
        f"⚡ <b>Rate:</b> 1 USDT = ₹{usdt_rate}\n\n"
        f"{list_text}\n"
        "Select an item to view details:</b></blockquote>"
    )

    if is_cb:
        try: await message_or_callback.answer()
        except: pass
        await msg.edit_text(header_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
    else:
        await msg.reply_text(header_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)



# ==================================================================
# 📂 STEP 2: BUCKET SELECTION (Inside Country)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^(country|page_cty)_(accounts|sessions)_(.+)"))
async def show_country_products(c, cb):
    try:
        data = cb.data
        page = 1
        
        # EXTRACTION FOR COUNTRIES WITH SPACES/UNDERSCORES
        if data.startswith("page_cty_"):
            remainder, page_str = data.replace("page_cty_", "").rsplit("_", 1)
            category, country_name = remainder.split("_", 1)
            page = int(page_str)
        else:
            remainder = data.replace("country_", "")
            category, country_name = remainder.split("_", 1)

        item_type = "session" if category == "sessions" else "account"
        buckets = await get_buckets_by_country(country_name, item_type)
        
        if not buckets:
            return await cb.answer(f"⚠️ Stock just finished for {country_name}!", show_alert=True)

        usdt_rate = await get_live_usdt_rate()
        items_list = []
        for b in buckets:
            p_id = b["_id"]
            price = b["price"]
            year = b["year"]
            count = b["count"]
            flag = b.get("flag")
            if not flag or flag == "🏳️":
                from utils import get_country_info
                c_info = get_country_info(country_name)
                flag = c_info.get("flag") or "🏳️"

            price_usdt = round(price / usdt_rate, 2)
            btn_text = f"{flag} {year} - ₹{price} (${price_usdt}) [{count}]"
            cb_data = f"pb|{category}|{country_name}|{year}|{price}"
            items_list.append({
                "text": btn_text,
                "callback_data": cb_data
            })

        kb = get_pagination_keyboard(
            current_page=page,
            total_count=len(items_list),
            data_list=items_list,
            callback_prefix=f"page_cty_{category}_{country_name}",
            row_width=1
        )
        
        kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back to Countries", callback_data=f"cat_{category}")])

        header_text = (
            f"<b>🚩 {country_name.upper()} - {category.upper()}</b>\n"
            f"{get_divider()}\n"
            f"⚡ <b>Rate:</b> 1 USDT = ₹{usdt_rate}\n"
            "👇 <b>Select a product bucket below:</b>"
        )
        
        await cb.answer() # STOPS THE LOADING SPINNER
        await cb.message.edit_text(header_text, parse_mode=enums.ParseMode.HTML, reply_markup=kb)
        
    except Exception as e:
        print(f"Product Pagination Error: {e}")
        await cb.answer("❌ Error loading products!", show_alert=True)



# ==================================================================
# 🚥 STEP 3: CONFIRMATION SCREEN
# ==================================================================

@Client.on_callback_query(filters.regex(r"^pb\|(.+)\|(.+)\|(.+)\|(.+)"))
async def confirm_purchase_ui(c, cb):
    try:
        # pb|category|country|year|price
        _, category, country, year, price = cb.data.split("|")
        item_type = "session" if category == "sessions" else "account"
        
        # Get Latest Stock Count
        stock = await get_stock_count(country, item_type, int(price), year)
        if stock <= 0:
            return await cb.answer("⚠️ Out of Stock!", show_alert=True)

        usdt_rate = await get_live_usdt_rate()
        user = await get_user(cb.from_user.id)
        balance_inr = user.get("balance", 0.0) if user else 0.0
        
        price_inr = int(price)
        price_usdt = round(price_inr / usdt_rate, 3)
        can_buy = balance_inr >= price_inr

        # Try to find a flag (purely aesthetic)
        from database import col_stock
        sample = await col_stock.find_one({"country": country, "status": "fresh"})
        flag = sample.get("flag") if sample else None
        if not flag or flag == "🏳️":
            from utils import get_country_info
            c_info = get_country_info(country)
            flag = c_info.get("flag") or "🏳️"

        text = (
            f"🛒 <b>CONFIRM PURCHASE</b>\n"
            f"{get_divider()}\n"
            f"📦 <b>Item:</b> {flag} {country} ({year})\n"
            f"💰 <b>Price:</b> ${price_usdt} (₹{price_inr})\n"
            f"💳 <b>Your Wallet:</b> ₹{balance_inr}\n"
            f"📊 <b>Stock Available:</b> {stock}\n"
            f"{get_divider()}\n"
            "🟢 <b>Safe & Tested Accounts</b>\n"
            "⚠️ <i>No Guarantee after login. Buy at own risk.</i>"
        )

        if can_buy:
            text += "\n\n✅ <i>Sufficient balance available.</i>"
            # eb|cat|country|year|price
            confirm_btn = InlineKeyboardButton("✅ Pay & Get Item", callback_data=f"eb|{category}|{country}|{year}|{price}")
        else:
            text += f"\n\n❌ <b>Insufficient Funds!</b>\nNeed ₹{round(price_inr - balance_inr, 2)} more."
            confirm_btn = InlineKeyboardButton("➕ Deposit Funds", callback_data="deposit_home")

        buttons = InlineKeyboardMarkup([
            [confirm_btn],
            [InlineKeyboardButton("🔙 Back", callback_data=f"cat_{category}")]
        ])
        
        await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)

    except Exception as e:
        print(f"Confirmation Error: {e}")
        await cb.answer("Error fetching details.", show_alert=True)

# ==================================================================
# ✅ STEP 4: EXECUTION & DELIVERY
# ==================================================================

@Client.on_callback_query(filters.regex(r"^eb\|(.+)\|(.+)\|(.+)\|(.+)"))
async def execute_order(c, cb):
    try:
        _, category, country, year, price = cb.data.split("|")
        item_type = "session" if category == "sessions" else "account"
        user_id = cb.from_user.id
        
        await cb.answer("🚀 Processing Order...", show_alert=False)
        
        # 1. ATOMIC BUCKET TRANSACTION
        purchased_item = await buy_bucket_atomic(user_id, country, year, int(price), item_type)
        
        if not purchased_item:
            return await cb.answer("❌ Transaction Failed! Stock out or Low Balance.", show_alert=True)
    except Exception as e:
        print(f"Order Execution Error: {e}")
        return await cb.answer("❌ Error processing order.", show_alert=True)
    
    # 2. Extract Data
    item_data = purchased_item.get("data", "N/A") # This is the Session String
    phone_number = purchased_item.get("phone", "Unknown") 
    
    order_id = purchased_item["_id"]
    price = purchased_item.get("price", 0)
    country = purchased_item.get("country", "Unknown")
    flag = purchased_item.get("flag", "🏳️")

    # 3. DELIVERY LOGIC SWITCH
    
    # CASE A: ACCOUNTS (Login Assistant - Shows Phone & Session Download option)
    if category == "accounts":
        assistant_text = (
            "<b>📲 LOGIN ASSISTANT</b>\n"
            f"{get_divider()}\n"
            f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
            f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n\n"
            "<b>📩 Latest Code:</b> <code>Waiting for request...</code>\n\n"
            "ℹ️ <i>Click 'Get Code' AFTER entering the number in Telegram.</i>"
        )
        
        btns = [
            [InlineKeyboardButton("🔄 Get Code", callback_data=f"otp_{order_id}")],
        ]
        if item_data and item_data != "N/A":
            btns.append([InlineKeyboardButton("📥 Download Session File", callback_data=f"dl_session_{order_id}")])
        btns.append([InlineKeyboardButton("📱 Manage Logins", callback_data=f"mng_{order_id}")])
        btns.append([InlineKeyboardButton("✅ Done", callback_data="home")])
        
        await cb.message.edit_text(assistant_text, parse_mode=enums.ParseMode.HTML, reply_markup=InlineKeyboardMarkup(btns))

    # CASE B: SESSIONS (File Delivery + OTP option)
    else:
        success_text = (
            "<b>✅ ORDER SUCCESSFUL!</b>\n"
            f"{get_divider()}\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"🏳️ <b>Country:</b> {flag} {country}\n"
            f"💰 <b>Price:</b> ₹{price}\n"
            f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
            f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n"
            f"{get_divider()}\n"
            "👇 <b>SESSION FILE & STRING BELOW</b>\n"
            "<i>Download the session file, copy the string, or get login OTP directly.</i>"
        )
        await cb.message.edit_text(
            success_text, 
            parse_mode=enums.ParseMode.HTML, 
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Get Login OTP", callback_data=f"otp_{order_id}")],
                [InlineKeyboardButton("📱 Manage Logins", callback_data=f"mng_{order_id}")],
                [InlineKeyboardButton("🛍 Buy Again", callback_data=f"cat_{category}")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ])
        )
        
        # Deliver session document + string
        await deliver_session_to_user(c, user_id, order_id, item_data, phone_number, country, flag)

    # 4. Public Log
    await send_public_log(c, cb.from_user, country, price, phone_number, flag)


# ==================================================================
# 📥 HELPER: DELIVER SESSION FILE & STRING
# ==================================================================

async def deliver_session_to_user(client, user_id, order_id, item_data, phone_number, country, flag):
    """Generates the appropriate .session file and delivers it to the user."""
    try:
        from utils.session_patch import normalize_session_string, normalize_session_bytes, session_tuple_to_sqlite_bytes
        
        clean_string = normalize_session_string(item_data, default_api_id=API_ID) if item_data else item_data

        filename = f"{phone_number}_{country}.session" if phone_number and phone_number != "Unknown" else f"Session_{order_id}.session"
        file_path = f"downloads/{filename}"
        
        if not os.path.exists("downloads"):
            os.makedirs("downloads", exist_ok=True)
        
        # Check if item_data is base64-encoded binary SQLite session
        is_sqlite_binary = False
        raw_bytes = None
        if item_data:
            try:
                decoded_bytes = base64.b64decode(item_data.encode("utf-8"), validate=True)
                if decoded_bytes.startswith(b"SQLite format 3\x00"):
                    is_sqlite_binary = True
                    raw_bytes = decoded_bytes
            except Exception:
                pass

        # Always generate a 100% Universal SQLite .session database file (Telethon + Pyrogram + Hydrogram compatible)
        universal_bytes = None
        try:
            if clean_string:
                pad = "=" * (-len(clean_string) % 4)
                try:
                    raw_session = base64.urlsafe_b64decode(clean_string + pad)
                except Exception:
                    raw_session = base64.b64decode(clean_string + pad)
                dc_id, api_id, test_mode, auth_key, user_id_val, is_bot = normalize_session_bytes(raw_session, API_ID)
                universal_bytes = session_tuple_to_sqlite_bytes(dc_id, api_id, test_mode, auth_key, user_id_val, phone_number)
            elif is_sqlite_binary and raw_bytes:
                dc_id, api_id, test_mode, auth_key, user_id_val, is_bot = normalize_session_bytes(raw_bytes, API_ID)
                universal_bytes = session_tuple_to_sqlite_bytes(dc_id, api_id, test_mode, auth_key, user_id_val, phone_number)
        except Exception as err:
            logger.error(f"Error generating universal sqlite session: {err}")

        if universal_bytes:
            with open(file_path, "wb") as f:
                f.write(universal_bytes)
        elif is_sqlite_binary and raw_bytes:
            with open(file_path, "wb") as f:
                f.write(raw_bytes)
        else:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(str(clean_string or item_data))
        
        # 1. Send Document
        caption = (
            f"📂 <b>Session File Delivered</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
            f"🏳️ <b>Country:</b> {flag} {country}\n"
            f"📞 <b>Phone:</b> <code>{phone_number}</code>\n"
            f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n\n"
            f"⚠️ <i>Keep this file safe and do not share it.</i>"
        )
        await client.send_document(
            chat_id=user_id,
            document=file_path,
            file_name=filename,
            caption=caption,
            parse_mode=enums.ParseMode.HTML
        )
        
        # 2. Send Copyable Session String
        copyable_str = clean_string if (clean_string and len(clean_string) > 20 and not clean_string.startswith("SQLite")) else None
        if copyable_str:
            await client.send_message(
                chat_id=user_id,
                text=(
                    f"🔑 <b>Session String (1-Tap Copy):</b>\n\n"
                    f"<code>{copyable_str}</code>"
                ),
                parse_mode=enums.ParseMode.HTML
            )
            
        # Clean up local file
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass
            
    except Exception as e:
        print(f"Error delivering session to user: {e}")
        try:
            await client.send_message(
                user_id,
                f"❌ <b>Error delivering file.</b>\nYour session data:\n<code>{item_data[:100]}...</code>\nPlease contact support with Order ID: <code>{order_id}</code>",
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass


# ==================================================================
# 📂 VIEW & RE-DOWNLOAD PURCHASED SESSIONS
# ==================================================================

@Client.on_callback_query(filters.regex(r"^view_session_([a-zA-Z0-9-]+)"))
async def view_session_handler(c, cb):
    order_id = cb.data.replace("view_session_", "", 1)
    from plugins.manager import fetch_order, get_session_from_order
    order = await fetch_order(order_id)
    if not order:
        return await cb.answer("❌ Order not found!", show_alert=True)
        
    phone = order.get("phone", "Unknown")
    country = order.get("country", "Unknown")
    flag = order.get("flag", "🏳️")
    price = order.get("price", 0)
    session_data = await get_session_from_order(order)
    
    text = (
        f"<b>📂 ORDER DETAILS</b>\n"
        f"{get_divider()}\n"
        f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
        f"🏳️ <b>Country:</b> {flag} {country}\n"
        f"📞 <b>Phone:</b> <code>{phone}</code>\n"
        f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"{get_divider()}\n"
    )
    if session_data and not session_data.startswith("SQLite") and len(session_data) > 10:
        text += f"🔑 <b>Session String:</b>\n<code>{session_data}</code>\n\n"
    text += "<i>Choose an option below:</i>"
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Get OTP", callback_data=f"otp_{order_id}")],
        [InlineKeyboardButton("📥 Download Session File", callback_data=f"dl_session_{order_id}")],
        [InlineKeyboardButton("📱 Manage Logins", callback_data=f"mng_{order_id}")],
        [InlineKeyboardButton("🔙 Back to Orders", callback_data="my_orders_list")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


@Client.on_callback_query(filters.regex(r"^dl_session_([a-zA-Z0-9-]+)"))
async def download_session_callback(c, cb):
    order_id = cb.data.replace("dl_session_", "", 1)
    from plugins.manager import fetch_order, get_session_from_order
    order = await fetch_order(order_id)
    if not order:
        return await cb.answer("❌ Order not found!", show_alert=True)
    
    session_data = await get_session_from_order(order)
    if not session_data:
        return await cb.answer("❌ Session data missing!", show_alert=True)
        
    await cb.answer("📥 Sending session file...")
    phone = order.get("phone", "Unknown")
    country = order.get("country", "Unknown")
    flag = order.get("flag", "🏳️")
    await deliver_session_to_user(c, cb.from_user.id, order_id, session_data, phone, country, flag)

# ==================================================================
# 📢 STEP 5: ADVANCED LOGGING (Ultra Aesthetic & Optimized)
# ==================================================================

async def send_public_log(client, user, country, price, item_data, flag):
    try:
        # Number Masking Logic (+91986••••••)
        raw = str(item_data).replace("+", "")
        if len(raw) > 5:
            masked = f"+{raw[:5]}••••••"
        else:
            masked = "+••••••••"

        # User ID Masking (828••••082)
        uid_str = str(user.id)
        if len(uid_str) > 6:
            masked_uid = f"{uid_str[:3]}••••{uid_str[-3:]}"
        else:
            masked_uid = "••••••••"

        log_text = (
            "<pre><code> ✅ New Number Purchase Successful </code></pre>\n\n"
            f"<b>━ <u>Country</u>:</b>  <b>{country.title()}</b> {flag}\n"
            f"<b>━ <u>Application</u>:</b>  <i><b>Tеlеgгaм</b></i> 🍷\n\n"
            f"<b>✚ <u>Number</u>:</b>  <code>{masked}</code> 📞\n"
            f"<b>✚ <u>OTP</u>:</b>  <spoiler>******</spoiler> 💬\n"
            f"<b>✚ <u>Server</u>:</b>  <b>(1)</b> 🥂\n"
            f"<b>✚ <u>Password</u>:</b>  <spoiler>{STATIC_2FA_PASSWORD}</spoiler> 🔐\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 <b>User ID:</b> <code>{masked_uid}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"<b>✦</b> <i>@OTP_SELLER143_BOT</i>  <b>||</b>  <i>@OTP_SELLER143_BOT</i> <b>✦</b>"
        )
        
        # Hardcoded URL for optimization
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🛒 • Buy Now • 🛒", url="https://t.me/OTP_SELLER143_BOT")]
        ])
        
        if LOG_CHANNEL and LOG_CHANNEL != 0:
            await client.send_message(
                LOG_CHANNEL, 
                log_text, 
                parse_mode=enums.ParseMode.HTML, 
                disable_web_page_preview=True,
                reply_markup=buttons
            )
    except Exception as e:
        print(f"Logging Error: {e}")
