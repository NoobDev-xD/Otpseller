import os
import re
import json
import base64
import logging
import asyncio
import zipfile
import tempfile
import shutil
try:
    import pycountry  # type: ignore
except ImportError:
    pycountry = None

logger = logging.getLogger(__name__)
from datetime import datetime
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from config import ADMINS, LOG_CHANNEL, CREATOR_ID, API_ID, API_HASH, STATIC_2FA_PASSWORD
from database import (
    db, add_stock, get_unique_buckets, get_user, update_balance, 
    col_users, col_stock, col_orders, col_payments, col_settings,
    get_unique_countries, get_buckets_by_country, update_fsub, get_fsub_list, del_fsub,
    update_usdt_rate, set_maintenance, get_maintenance, update_bucket_price
)

# ==================================================================
# 🧠 ADMIN STATE MANAGEMENT (RAM)
# ==================================================================

admin_session = {}

def clear_session(user_id):
    """Safely clears any active admin listener and disconnects temp clients."""
    if user_id in admin_session:
        session = admin_session.pop(user_id, None)
        if session and isinstance(session, dict):
            temp_cli = session.get("temp_client")
            if temp_cli and getattr(temp_cli, "is_connected", False):
                try:
                    asyncio.create_task(temp_cli.disconnect())
                except:
                    pass

def parse_stock_meta_input(text, default_type="account"):
    """
    Robustly parses inputs like:
    - 'USA 100 2024'
    - 'United States 100 2024 session'
    - 'Venezuela 16 permspam session'
    Returns: (country, price, year, item_type) or None
    """
    if not text:
        return None
    parts = text.strip().split()
    if len(parts) < 3:
        return None

    last_token = parts[-1].lower().rstrip("s")
    if last_token in ["account", "session"]:
        item_type = last_token
        remaining = parts[:-1]
    else:
        item_type = default_type
        remaining = parts

    if len(remaining) < 3:
        return None

    # Standard format: Country (1+ words) Price Year
    try:
        price = int(remaining[-2])
        year = str(remaining[-1])
        raw_country = " ".join(remaining[:-2]).strip()
        if raw_country:
            return raw_country, price, year, item_type
    except (ValueError, TypeError):
        pass

    # Search backwards for the numeric price token
    for i in range(len(remaining) - 1, 0, -1):
        if remaining[i-1].isdigit():
            price = int(remaining[i-1])
            year = str(remaining[i])
            raw_country = " ".join(remaining[:i-1]).strip()
            if raw_country:
                return raw_country, price, year, item_type

    return None

# ==================================================================
# 🛠️ HELPER: SAFE SEND
# ==================================================================
async def safe_show_dashboard(client, message_or_callback):
    """
    Shows Main Admin Dashboard. Robust fix for Back Button Crash.
    """
    user_id = message_or_callback.from_user.id
    if isinstance(message_or_callback, CallbackQuery):
        await message_or_callback.answer() # Remove Spinner IMMEDIATELY
    clear_session(user_id)

    total_users = await col_users.count_documents({})
    total_stock = await col_stock.count_documents({"status": "fresh"})
    pending_crypto = await col_payments.count_documents({"status": "pending"})
    total_sales = await col_orders.count_documents({})

    text = (
        "<b>👮‍♂️ ULTIMATE ADMIN DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 <b>Total Users:</b> {total_users}\n"
        f"📦 <b>Active Stock:</b> {total_stock}\n"
        f"💰 <b>Total Sales:</b> {total_sales}\n"
        f"⏳ <b>Pending Payments:</b> {pending_crypto}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Welcome back, Admin. Select an action:</i>"
    )

    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Stock Manager", callback_data="admin_stock"),
            InlineKeyboardButton(f"💰 Payments ({pending_crypto})", callback_data="admin_payments")
        ],
        [
            InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
            InlineKeyboardButton("👤 User Manager", callback_data="admin_users")
        ],
        [
            InlineKeyboardButton("⚙️ Settings & FSub", callback_data="admin_settings"),
            InlineKeyboardButton("✏️ Edit Prices", callback_data="admin_edit_price")
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="close_admin")
        ]
    ])

    try:
        if isinstance(message_or_callback, CallbackQuery):
            try:
                await message_or_callback.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
            except Exception as e:
                if "MESSAGE_NOT_MODIFIED" in str(e):
                    return
                await message_or_callback.message.delete()
                await client.send_message(user_id, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        else:
            await message_or_callback.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            return
        try:
            await client.send_message(user_id, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        except:
            pass


# ==================================================================
# 🏠 ENTRY POINTS
# ==================================================================

@Client.on_message(filters.command("admin") & filters.user(ADMINS))
async def admin_panel(client, message):
    await safe_show_dashboard(client, message)

@Client.on_callback_query(filters.regex("admin_home"))
async def home_callback(c, cb):
    await safe_show_dashboard(c, cb)

# ==================================================================
# 📦 STOCK MANAGER (With 2-Step View)
# ==================================================================

@Client.on_callback_query(filters.regex("admin_stock"))
async def stock_menu(c, cb):
    await cb.answer()
    clear_session(cb.from_user.id)
    text = (
        "<b>📦 STOCK MANAGEMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<b>📂 Present Stock:</b> View and add to existing countries.\n"
        "<b>🆕 New Bucket:</b> Create a new country/price category.\n"
        "<b>📤 ZIP Upload:</b> Bulk add sessions from a ZIP file.\n"
        "<b>🗑 Clear Stock:</b> Delete items by country."
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Present Stock (View All)", callback_data="goto_present_admin")],
        [InlineKeyboardButton("🆕 New Bucket (Quick Create)", callback_data="goto_new_admin")],
        [InlineKeyboardButton("📤 Upload ZIP Sessions", callback_data="upload_zip_sessions")],
        [
            InlineKeyboardButton("🔍 Search & Delete", callback_data="search_stock_dlt"),
            InlineKeyboardButton("🗑 Bulk Clear", callback_data="admin_delete_menu")
        ],
        [InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_home")]
    ])
    try:
        await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            return
        await cb.message.reply_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

# Step 1: Admin Country List
@Client.on_callback_query(filters.regex("goto_present_admin"))
async def show_present_countries(c, cb):
    await cb.answer()
    countries = await get_unique_countries()
    if not countries:
        return await cb.answer("⚠️ No stock found in DB!", show_alert=True)
    
    buttons = []
    for item in countries:
        name = item["_id"]
        flag = item.get("flag", "🏳️")
        buttons.append([InlineKeyboardButton(f"{flag} {name}", callback_data=f"adm_cty_{name}")])
    
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_stock")])
    await cb.message.edit_text("<b>🌍 Select Country to Manage:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

# Step 2: Show Buckets in that country for Admin
@Client.on_callback_query(filters.regex(r"^adm_cty_(.+)"))
async def show_country_buckets_admin(c, cb):
    await cb.answer()
    country_name = cb.data.replace("adm_cty_", "", 1)
    buckets = await get_buckets_by_country(country_name)
    
    settings = await col_settings.find_one({"_id": "main_config"}) or {}
    try:
        usdt_rate = float(settings.get("usdt_rate", 90.0))
        if usdt_rate <= 0: usdt_rate = 90.0
    except:
        usdt_rate = 90.0

    buttons = []
    for b in buckets:
        price = b['price']
        price_usdt = round(price / usdt_rate, 2)
        btn_text = f"{b.get('flag','🏳️')} {b['year']} - ₹{price} (${price_usdt}) [{b['count']}] ({b['type']})"
        cb_data = f"pre_upload_{country_name}_{b['price']}_{b['year']}_{b['type']}"
        buttons.append([InlineKeyboardButton(btn_text, callback_data=cb_data)])
    
    buttons.append([InlineKeyboardButton("🔙 Back to Countries", callback_data="goto_present_admin")])
    try:
        await cb.message.edit_text(f"<b>🚩 Managing: {country_name}</b>\nClick a bucket to add more stock:", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    except Exception:
        pass

# ==================================================================
# 🗑️ BULK STOCK DELETER
# ==================================================================

@Client.on_callback_query(filters.regex("admin_delete_menu"))
async def admin_delete_menu(c, cb):
    text = (
        "<b>🗑️ BULK STOCK DELETER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>WARNING:</b> This action cannot be undone.\n\n"
        "<b>🧹 Clear All:</b> Deletes every single item in the bot.\n"
        "<b>🌍 Clear by Country:</b> Deletes only items from a specific country."
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧹 DELETE ALL STOCK", callback_data="confirm_delete_all")],
        [InlineKeyboardButton("🌍 DELETE BY COUNTRY", callback_data="delete_by_country_list")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_stock")]
    ])
    await cb.answer()
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex("confirm_delete_all"))
async def confirm_delete_all(c, cb):
    await cb.message.edit_text(
        "<b>🛑 FINAL WARNING!</b>\n"
        "Are you absolutely sure you want to <b>DELETE ALL STOCK</b>?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES, WIPE EVERYTHING", callback_data="execute_delete_all")],
            [InlineKeyboardButton("❌ NO, CANCEL", callback_data="admin_delete_menu")]
        ]),
        parse_mode=enums.ParseMode.HTML
    )
    await cb.answer()

@Client.on_callback_query(filters.regex("execute_delete_all"))
async def execute_delete_all(c, cb):
    await col_stock.delete_many({})
    await cb.answer("💥 Database Wiped Clean!", show_alert=True)
    await stock_menu(c, cb)

@Client.on_callback_query(filters.regex("delete_by_country_list"))
async def delete_by_country_list(c, cb):
    countries = await get_unique_countries()
    if not countries:
        return await cb.answer("No stock found!", show_alert=True)
    
    await cb.answer()
    buttons = []
    for item in countries:
        name = item["_id"]
        buttons.append([InlineKeyboardButton(f"🗑 Delete {name}", callback_data=f"conf_dlt_cty_{name}")])
    
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_delete_menu")])
    try:
        await cb.answer()
        await cb.message.edit_text("<b>🌍 Select Country to WIPE:</b>", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)
    except:
        pass

@Client.on_callback_query(filters.regex(r"conf_dlt_cty_(.+)"))
async def confirm_delete_country(c, cb):
    country = cb.data.replace("conf_dlt_cty_", "")
    await cb.message.edit_text(
        f"<b>🛑 CONFIRM WIPE: {country.upper()}</b>\n"
        f"Are you sure you want to delete all {country} stock?",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ YES, DELETE", callback_data=f"exec_dlt_cty_{country}")],
            [InlineKeyboardButton("❌ NO, CANCEL", callback_data="delete_by_country_list")]
        ]),
        parse_mode=enums.ParseMode.HTML
    )
    await cb.answer()

@Client.on_callback_query(filters.regex(r"exec_dlt_cty_(.+)"))
async def execute_delete_country(c, cb):
    country = cb.data.replace("exec_dlt_cty_", "")
    res = await col_stock.delete_many({"country": country})
    await cb.answer(f"✅ Deleted {res.deleted_count} items from {country}!", show_alert=True)
    await delete_by_country_list(c, cb)

# Create New Bucket Trigger
@Client.on_callback_query(filters.regex("goto_new_admin"))
async def new_bucket_ask(c, cb):
    admin_session[cb.from_user.id] = {"mode": "smart_input", "menu_id": cb.message.id}
    await cb.answer()
    await cb.message.edit_text(
        "<b>🆕 Smart Bucket Creator</b>\n\n"
        "Send: <code>Country Price Year</code>\n"
        "Example: <code>USA 100 2024</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_stock")]]),
        parse_mode=enums.ParseMode.HTML
    )

# ==================================================================
# 📤 ZIP SESSION UPLOADER
# ==================================================================

@Client.on_callback_query(filters.regex("upload_zip_sessions"))
async def upload_zip_trigger(c, cb):
    admin_session[cb.from_user.id] = {
        "mode": "awaiting_zip",
        "menu_id": cb.message.id
    }
    await cb.message.edit_text(
        "<b>📤 ZIP SESSION UPLOADER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send a <b>.zip file</b> containing <code>.session</code> files.\n\n"
        "<b>📋 Format Rules:</b>\n"
        "• File names: <code>959758160960.session</code>\n"
        "• The number = phone number of the account\n"
        "• All sessions will be added as <b>fresh stock</b>\n\n"
        "<b>⚙️ After sending the ZIP, I will ask for:</b>\n"
        "Country, Price, Year, Type\n\n"
        "<i>(Type can be 'account' or 'session')</i>\n\n"
        "<i>📎 Send the ZIP file now...</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Cancel", callback_data="admin_stock")]
        ]),
        parse_mode=enums.ParseMode.HTML
    )
    await cb.answer()


@Client.on_message(filters.user(ADMINS) & filters.document)
async def handle_zip_upload(c, msg):
    user_id = msg.from_user.id
    session = admin_session.get(user_id)

    if not session or session.get("mode") != "awaiting_zip":
        return

    doc = msg.document
    file_name = doc.file_name.lower()
    is_zip = file_name.endswith(".zip")
    is_direct_session = file_name.endswith(".session") or file_name.endswith(".txt") or file_name.endswith(".json")

    if not (is_zip or is_direct_session):
        temp = await msg.reply("❌ Please send a <b>.zip</b>, <b>.session</b>, <b>.txt</b>, or <b>.json</b> file!", parse_mode=enums.ParseMode.HTML)
        await asyncio.sleep(3)
        await temp.delete()
        return

    status = await msg.reply("📥 <b>Downloading File...</b>", parse_mode=enums.ParseMode.HTML)

    try:
        tmp_dir = tempfile.mkdtemp()
        session_files = []

        if is_zip:
            zip_path = os.path.join(tmp_dir, "sessions.zip")
            await c.download_media(msg, file_name=zip_path)

            extract_dir = os.path.join(tmp_dir, "extracted")
            os.makedirs(extract_dir, exist_ok=True)

            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(extract_dir)

            # Find all session files, prioritizing .session and avoiding companion .json files
            for root, dirs, files in os.walk(extract_dir):
                session_stems = set()
                # 1. First collect all .session files
                for f in files:
                    lower_f = f.lower()
                    if lower_f.endswith(".session"):
                        session_files.append(os.path.join(root, f))
                        stem = os.path.splitext(lower_f)[0]
                        session_stems.add(stem)

                # 2. Collect standalone .txt or .json files only if no corresponding .session file exists
                for f in files:
                    lower_f = f.lower()
                    stem = os.path.splitext(lower_f)[0]
                    if stem not in session_stems:
                        if lower_f.endswith(".txt") or lower_f.endswith(".json"):
                            session_files.append(os.path.join(root, f))
        else:
            direct_path = os.path.join(tmp_dir, doc.file_name)
            await c.download_media(msg, file_name=direct_path)
            session_files.append(direct_path)

        if not session_files:
            await status.edit_text("❌ <b>No session files found in the upload!</b>", parse_mode=enums.ParseMode.HTML)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            clear_session(user_id)
            return

        await status.edit_text(
            f"✅ <b>Files Extracted & Analyzed!</b>\n"
            f"📦 Found <b>{len(session_files)}</b> session file(s).\n\n"
            f"Now send: <code>Country Price Year Type</code>\n"
            f"Example: <code>India 150 2023 account</code>",
            parse_mode=enums.ParseMode.HTML
        )

        # Save session files list, move to next step
        admin_session[user_id] = {
            "mode": "zip_awaiting_meta",
            "session_files": session_files,
            "tmp_dir": tmp_dir,
            "menu_id": session.get("menu_id"),
            "status_msg_id": status.id
        }

    except zipfile.BadZipFile:
        await status.edit_text("❌ <b>Invalid or corrupted ZIP file!</b>", parse_mode=enums.ParseMode.HTML)
        clear_session(user_id)
    except Exception as e:
        await status.edit_text(f"❌ Error: <code>{str(e)[:100]}</code>", parse_mode=enums.ParseMode.HTML)
        clear_session(user_id)


# ==================================================================
# 👤 USER MANAGER
# ==================================================================

@Client.on_callback_query(filters.regex("admin_users"))
async def user_manager_menu(c, cb):
    clear_session(cb.from_user.id)
    text = (
        "<b>👤 USER MANAGER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Search a user by ID to modify balance or ban/unban them."
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Search User by ID", callback_data="search_user_input")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.answer()
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex("search_user_input"))
async def search_input_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "searching_user", "menu_id": cb.message.id}
    await cb.answer()
    await cb.message.edit_text(
        "<b>🔍 Enter User ID:</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_users")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"addmoney_(\d+)"))
async def add_money_trigger(c, cb):
    target_id = cb.data.split("_")[1]
    admin_session[cb.from_user.id] = {"mode": "adding_balance", "target_id": target_id, "menu_id": cb.message.id}
    await cb.answer()
    await cb.message.edit_text(
        f"<b>➕ Add Balance to `{target_id}`</b>\n\n"
        "Enter the amount in INR (Numbers only):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_users")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"deductmoney_(\d+)"))
async def deduct_money_trigger(c, cb):
    target_id = cb.data.split("_")[1]
    admin_session[cb.from_user.id] = {"mode": "deducting_balance", "target_id": target_id, "menu_id": cb.message.id}
    await cb.answer()
    await cb.message.edit_text(
        f"<b>➖ Deduct Balance from `{target_id}`</b>\n\n"
        "Enter the amount in INR to DEDUCT (Numbers only):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_users")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"ban_(\d+)"))
async def ban_user_callback(c, cb):
    target_id = int(cb.data.split("_")[1])
    await col_users.update_one({"_id": target_id}, {"$set": {"is_banned": True}})
    await cb.answer(f"🚫 User {target_id} Banned!", show_alert=True)
    await safe_show_dashboard(c, cb)

@Client.on_callback_query(filters.regex(r"unban_(\d+)"))
async def unban_user_callback(c, cb):
    target_id = int(cb.data.split("_")[1])
    await col_users.update_one({"_id": target_id}, {"$set": {"is_banned": False}})
    await cb.answer(f"✅ User {target_id} Unbanned!", show_alert=True)
    await safe_show_dashboard(c, cb)


# ==================================================================
# 💰 PAYMENTS
# ==================================================================

@Client.on_callback_query(filters.regex("admin_payments"))
async def admin_payments_menu(c, cb):
    await cb.answer()
    clear_session(cb.from_user.id)
    cursor = col_payments.find({"status": "pending"})
    txns = await cursor.to_list(length=5)
    
    if not txns:
        return await cb.answer("✅ All caught up! No pending payments.", show_alert=True)
    
    txn = txns[0]
    amount = txn.get("amount", "Unknown")
    method = txn.get("method", "Manual").upper()
    
    text = (
        "<b>💰 PENDING PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👤 User: `{txn['user_id']}`\n"
        f"💵 Amount: ₹{amount}\n"
        f"🪙 Method: {method}\n"
        f"📅 Date: {txn.get('date', 'N/A')}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Check Admin Group for screenshot/proof."
    )
    buttons = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"pay_approve_{txn['_id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"pay_reject_{txn['_id']}")
        ],
        [InlineKeyboardButton("➕ Manual Balance (UTR)", callback_data="manual_bal_input")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.answer()
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^pay_approve_(.+)"))
async def handle_pay_approve(c, cb):
    txn_id = cb.data.replace("pay_approve_", "", 1)
    txn = await col_payments.find_one({"_id": txn_id})
    if not txn:
        return await cb.answer("❌ Transaction not found!", show_alert=True)
    
    if txn.get("status") != "pending":
        return await cb.answer("⚠️ Transaction already processed!", show_alert=True)
    
    amount = float(txn.get("amount", 0))
    user_id = txn.get("user_id")
    
    await col_payments.update_one({"_id": txn_id}, {"$set": {"status": "success"}})
    await update_balance(user_id, amount)
    
    try:
        from database import check_referral_milestone
        await check_referral_milestone(user_id, amount)
    except Exception:
        pass
    
    try:
        await c.send_message(
            user_id,
            f"✅ <b>DEPOSIT APPROVED!</b>\n\n💰 <b>Added:</b> ₹{amount}\n👛 Check balance with /start",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass
    
    await cb.answer(f"✅ Approved ₹{amount} for user {user_id}!", show_alert=True)
    await admin_payments_menu(c, cb)

@Client.on_callback_query(filters.regex(r"^pay_reject_(.+)"))
async def handle_pay_reject(c, cb):
    txn_id = cb.data.replace("pay_reject_", "", 1)
    txn = await col_payments.find_one({"_id": txn_id})
    if not txn:
        return await cb.answer("❌ Transaction not found!", show_alert=True)
    
    if txn.get("status") != "pending":
        return await cb.answer("⚠️ Transaction already processed!", show_alert=True)
    
    amount = txn.get("amount", 0)
    user_id = txn.get("user_id")
    
    await col_payments.update_one({"_id": txn_id}, {"$set": {"status": "rejected"}})
    
    try:
        await c.send_message(
            user_id,
            f"❌ <b>DEPOSIT REJECTED</b>\n\nYour deposit of ₹{amount} was rejected by admin.\nIf this was a mistake, contact support.",
            parse_mode=enums.ParseMode.HTML
        )
    except Exception:
        pass
    
    await cb.answer(f"❌ Rejected deposit for user {user_id}!", show_alert=True)
    await admin_payments_menu(c, cb)

@Client.on_callback_query(filters.regex("^manual_bal_input$"))
async def manual_balance_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "manual_balance_utr", "menu_id": cb.message.id}
    await cb.answer()
    await cb.message.edit_text(
        "<b>➕ MANUAL BALANCE (UTR)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send: <code>UserID Amount UTR</code>\n"
        "Example: <code>123456789 500 401234567890</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_payments")]]),
        parse_mode=enums.ParseMode.HTML
    )


# ==================================================================
# 📢 BROADCAST
# ==================================================================

@Client.on_callback_query(filters.regex("admin_broadcast"))
async def bc_menu(c, cb):
    await cb.answer()
    clear_session(cb.from_user.id)
    text = "<b>📢 BROADCAST CENTER</b>\n\nSelect message type:"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📣 Simple Message", callback_data="bc_type_simple")],
        [InlineKeyboardButton("📌 Pin Message", callback_data="bc_type_pin")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_home")]
    ])
    await cb.answer()
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"bc_type_(.+)"))
async def bc_input_trigger(c, cb):
    b_type = cb.data.split("_")[2]
    admin_session[cb.from_user.id] = {"mode": "broadcasting", "type": b_type, "menu_id": cb.message.id}
    await cb.message.edit_text(
        f"<b>📢 Broadcast ({b_type.upper()})</b>\n\n"
        "Send the message (Text/Media/Forward) you want to send to ALL users.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_broadcast")]]),
        parse_mode=enums.ParseMode.HTML
    )
    await cb.answer()



# ==================================================================
# ⚙️ SETTINGS & FORCE SUB
# ==================================================================

@Client.on_callback_query(filters.regex("admin_settings"))
async def settings_menu(c, cb):
    await cb.answer()
    clear_session(cb.from_user.id)
    fsubs = await get_fsub_list()
    if fsubs:
        count = len(fsubs)
        first_id = fsubs[0].get("_id", "Unknown")
        fsub_text = f"✅ Active ({count} Channels)\n🆔 Main: {first_id}"
    else:
        fsub_text = "❌ Inactive"
    
    settings = await col_settings.find_one({"_id": "main_config"}) or {}
    usdt_rate = settings.get("usdt_rate", 90.0)
    maintenance = settings.get("maintenance", False)
    m_status = "🔴 ON" if maintenance else "🟢 OFF"
    
    text = (
        "<b>⚙️ BOT SETTINGS & CONFIG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"💵 <b>USDT Rate:</b> ₹{usdt_rate}\n"
        f"🚧 <b>Maintenance:</b> {m_status}\n"
        f"📢 <b>Force Sub:</b>\n{fsub_text}\n\n"
        "<i>Select a setting to modify:</i>"
    )
    buttons = [
        [InlineKeyboardButton("📢 Add FSub Channel", callback_data="set_fsub_input")],
        [InlineKeyboardButton("🗑 Clear All FSubs", callback_data="remove_fsub")],
        [InlineKeyboardButton("💵 Set USDT Rate", callback_data="set_usdt_input")],
    ]
    
    # Only Creator can toggle maintenance
    if cb.from_user.id == CREATOR_ID:
        buttons.append([InlineKeyboardButton(f"🚧 Toggle Maintenance", callback_data="toggle_maint")])
        
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])
    
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex("set_fsub_input"))
async def set_fsub_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "setting_fsub", "menu_id": cb.message.id}
    await cb.message.edit_text(
        "<b>📢 ADD FORCE SUBSCRIBE CHANNEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "1. Add me to the Channel/Group as Admin.\n"
        "2. Send the <b>Channel ID</b> here (e.g., -100xxxx).\n\n"
        "<i>I will auto-generate the invite link and add it to the list.</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings")]]),
        parse_mode=enums.ParseMode.HTML
    )
    await cb.answer()

@Client.on_callback_query(filters.regex("remove_fsub"))
async def remove_fsub_action(c, cb):
    from database import col_fsub
    await col_fsub.delete_many({})
    await cb.answer("✅ All Force Sub Channels Cleared!", show_alert=True)
    await settings_menu(c, cb)

@Client.on_callback_query(filters.regex("toggle_maint"))
async def toggle_maintenance_action(c, cb):
    if cb.from_user.id != CREATOR_ID:
        return await cb.answer("❌ Only the Bot Creator can toggle maintenance mode!", show_alert=True)
        
    current = await get_maintenance()
    new_status = not current
    await set_maintenance(new_status)
    status_text = "Enabled" if new_status else "Disabled"
    await cb.answer(f"✅ Maintenance {status_text}!", show_alert=True)
    await settings_menu(c, cb)

@Client.on_callback_query(filters.regex("set_usdt_input"))
async def set_usdt_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "setting_usdt", "menu_id": cb.message.id}
    await cb.message.edit_text(
        "<b>💵 SET USDT RATE</b>\n\n"
        "Send the new rate for <b>1 USDT</b> in INR.\n"
        "Example: <code>92.5</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_settings")]])
    )
    await cb.answer()

# ==================================================================
# 🤖 THE MASTER LISTENER (State Managed)
# ==================================================================

@Client.on_message(filters.user(ADMINS) & ~filters.command(["admin", "start", "ping"]), group=2)
async def admin_master_listener(c, msg):
    user_id = msg.from_user.id
    if user_id not in admin_session:
        return

    state = admin_session[user_id]
    mode = state.get("mode")
    menu_id = state.get("menu_id")

    try:
        # 1. SEARCH USER INPUT
        if mode == "searching_user" and msg.text:
            try:
                target_id = int(msg.text)
                user = await get_user(target_id)
                await msg.delete()
                if user:
                    info = (
                        f"👤 <b>User Found:</b> {user.get('name')}\n"
                        f"🆔 ID: `{target_id}`\n"
                        f"💰 Balance: ₹{user.get('balance', 0)}\n"
                        f"📅 Join: {user.get('join_date')}"
                    )
                    is_banned = user.get("is_banned", False)
                    ban_btn = InlineKeyboardButton("✅ Unban User", callback_data=f"unban_{target_id}") if is_banned else InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{target_id}")
                    btns = InlineKeyboardMarkup([
                        [
                            InlineKeyboardButton("➕ Add", callback_data=f"addmoney_{target_id}"),
                            InlineKeyboardButton("➖ Deduct", callback_data=f"deductmoney_{target_id}")
                        ],
                        [ban_btn],
                        [InlineKeyboardButton("🔙 Back", callback_data="admin_users")]
                    ])
                    await c.edit_message_text(msg.chat.id, menu_id, info, reply_markup=btns, parse_mode=enums.ParseMode.HTML)
                else:
                    temp = await msg.reply("❌ User not found!")
                    await asyncio.sleep(2)
                    await temp.delete()
            except:
                pass

        # 2. ADD BALANCE INPUT
        elif mode == "adding_balance" and msg.text:
            try:
                amount = int(msg.text)
                target = int(state["target_id"])
                await update_balance(target, amount)
                await msg.delete()
                await c.edit_message_text(msg.chat.id, menu_id, f"✅ Added ₹{amount} to User `{target}`",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="admin_home")]]), parse_mode=enums.ParseMode.HTML)
                clear_session(user_id)
            except:
                pass

        # 3. BROADCAST EXECUTION
        elif mode == "broadcasting":
            status = await msg.reply("🚀 <b>Processing Broadcast... Please wait.</b>", parse_mode=enums.ParseMode.HTML)
            users = col_users.find({})
            success_count = 0
            fail_count = 0
            should_pin = state.get("type") in ["bc_type_pin", "pin"]
            async for u in users:
                try:
                    target_id = int(u["_id"])
                    sent_msg = await msg.copy(chat_id=target_id)
                    if should_pin and sent_msg:
                        try:
                            await sent_msg.pin(both_sides=False)
                        except:
                            pass
                    success_count += 1
                    await asyncio.sleep(0.05)
                except Exception:
                    fail_count += 1
            clear_session(user_id)
            await status.edit_text(
                f"<b>📢 Broadcast Completed!</b>\n\n"
                f"✅ <b>Success:</b> {success_count}\n"
                f"❌ <b>Failed:</b> {fail_count}",
                parse_mode=enums.ParseMode.HTML
            )

        # 4. SMART STOCK INPUT
        elif mode == "smart_input" and msg.text:
            parsed = parse_stock_meta_input(msg.text, default_type="account")
            if not parsed:
                temp = await msg.reply(
                    "❌ <b>Invalid format!</b>\n"
                    "Format: <code>Country Price Year Type</code>\n"
                    "Example: <code>India 150 2024 account</code> (Type is optional, defaults to account)",
                    parse_mode=enums.ParseMode.HTML
                )
                await asyncio.sleep(4)
                await temp.delete()
                return
            try:
                from plugins.stock import manual_activate_upload
                raw_country, price, year, item_type = parsed
                
                try:
                    matches = pycountry.countries.search_fuzzy(raw_country) if pycountry else None
                    flag = "".join([chr(ord(c) + 127397) for c in matches[0].alpha_2]) if matches else "🏳️"
                    final_country = matches[0].name if matches else raw_country
                except:
                    final_country = raw_country
                    flag = "🏳️"
                await msg.delete()
                menu_msg = await c.get_messages(msg.chat.id, menu_id)
                await manual_activate_upload(c, menu_msg, final_country, price, year, flag, user_id, item_type)
            except Exception as e:
                print(f"Stock Input Error: {e}")

        # 5. DEDUCT BALANCE INPUT
        elif mode == "deducting_balance" and msg.text:
            try:
                amount = int(msg.text)
                target = int(state["target_id"])
                await update_balance(target, -amount)
                await msg.delete()
                await c.edit_message_text(msg.chat.id, menu_id, f"✅ Deducted ₹{amount} from User `{target}`",
                                          reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="admin_home")]]), parse_mode=enums.ParseMode.HTML)
                clear_session(user_id)
            except Exception as e:
                pass

        # 6. FORCE SUB CONFIGURATION
        elif mode == "setting_fsub" and msg.text:
            try:
                channel_id = int(msg.text)
                await msg.delete()
                try:
                    chat = await c.get_chat(channel_id)
                    link = await c.export_chat_invite_link(channel_id)
                    await update_fsub(channel_id, link, chat.title)
                    success_text = (
                        f"✅ <b>Force Sub Added!</b>\n"
                        f"📢 <b>Channel:</b> {chat.title}\n"
                        f"🔗 <b>Link:</b> {link}\n\n"
                        "<i>Users must join ALL active channels.</i>"
                    )
                    await c.edit_message_text(
                        msg.chat.id, menu_id, success_text,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]])
                    )
                    clear_session(user_id)
                except Exception as e:
                    temp = await msg.reply(f"❌ Error: Make sure I am Admin there!\n{e}")
                    await asyncio.sleep(5)
                    await temp.delete()
            except ValueError:
                temp = await msg.reply("❌ Invalid ID! Must be an integer (e.g. -100...)")
                await asyncio.sleep(3)
                await temp.delete()

        # 7. USDT RATE SETTING
        elif mode == "setting_usdt" and msg.text:
            try:
                rate = float(msg.text)
                await update_usdt_rate(rate)
                await msg.delete()
                await c.edit_message_text(
                    msg.chat.id, menu_id, f"✅ <b>USDT Rate Updated:</b> ₹{rate}",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_settings")]])
                )
                clear_session(user_id)
            except ValueError:
                pass

        # 8. MANUAL BALANCE UTR INPUT
        elif mode == "manual_balance_utr" and msg.text:
            parts = msg.text.strip().split()
            if len(parts) < 3:
                temp = await msg.reply("❌ Format: <code>UserID Amount UTR</code>\nExample: <code>123456789 500 401234567890</code>", parse_mode=enums.ParseMode.HTML)
                await asyncio.sleep(3)
                await temp.delete()
                return
            try:
                target_id = int(parts[0])
                amount = float(parts[1])
                utr = parts[2]
                
                from database import create_deposit
                dep_res = await create_deposit(target_id, amount, utr, method="manual", status="success")
                if dep_res == "duplicate":
                    temp = await msg.reply(f"⚠️ UTR `{utr}` already exists in database!", parse_mode=enums.ParseMode.HTML)
                    await asyncio.sleep(4)
                    await temp.delete()
                    return
                
                await update_balance(target_id, amount)
                try:
                    await c.send_message(
                        target_id,
                        f"✅ <b>DEPOSIT CREDITED!</b>\n\n💰 <b>Added:</b> ₹{amount}\n🆔 <b>UTR:</b> <code>{utr}</code>\n👛 Check balance with /start",
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception:
                    pass
                
                await msg.delete()
                await c.edit_message_text(
                    msg.chat.id, menu_id,
                    f"✅ <b>Balance Added!</b>\n\n👤 <b>User:</b> `{target_id}`\n💰 <b>Amount:</b> ₹{amount}\n🆔 <b>UTR:</b> <code>{utr}</code>",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Dashboard", callback_data="admin_home")]]),
                    parse_mode=enums.ParseMode.HTML
                )
                clear_session(user_id)
            except ValueError:
                temp = await msg.reply("❌ UserID and Amount must be numbers!")
                await asyncio.sleep(3)
                await temp.delete()

        # 9. ZIP SESSION META INPUT
        elif mode == "zip_awaiting_meta" and msg.text:
            parsed = parse_stock_meta_input(msg.text, default_type="session")
            if not parsed:
                temp = await msg.reply(
                    "❌ <b>Invalid format!</b>\n"
                    "Format: <code>Country Price Year Type</code>\n"
                    "Example: <code>India 150 2023 session</code> (Type is optional, defaults to session)",
                    parse_mode=enums.ParseMode.HTML
                )
                await asyncio.sleep(4)
                await temp.delete()
                return

            try:
                raw_country, price, year, item_type = parsed

                session_files = state.get("session_files", [])
                tmp_dir = state.get("tmp_dir")

                # Flag detection
                try:
                    matches = pycountry.countries.search_fuzzy(raw_country) if pycountry else None
                    flag = "".join([chr(ord(ch) + 127397) for ch in matches[0].alpha_2]) if matches else "🏳️"
                    final_country = matches[0].name if matches else raw_country
                except:
                    final_country = raw_country
                    flag = "🏳️"

                await msg.delete()
                status = await c.send_message(
                    msg.chat.id,
                    f"⚡ <b>Adding {len(session_files)} sessions to stock...</b>",
                    parse_mode=enums.ParseMode.HTML
                )

                valid_sessions = []
                failed = 0

                for sf in session_files:
                    try:
                        filename = os.path.basename(sf)
                        phone = re.sub(r"\.(session|json|txt)$", "", filename, flags=re.IGNORECASE)
                        json_meta = {}

                        # Check for matching .json file
                        json_file = sf.rsplit(".", 1)[0] + ".json"
                        if os.path.exists(json_file):
                            try:
                                with open(json_file, "r", encoding="utf-8", errors="ignore") as jf:
                                    json_meta = json.load(jf)
                            except Exception:
                                pass

                        # Read session file content
                        with open(sf, "rb") as f:
                            session_data = f.read()

                        if sf.lower().endswith(".json"):
                            try:
                                json_meta = json.loads(session_data.decode("utf-8", errors="ignore"))
                            except Exception:
                                pass

                        if json_meta and isinstance(json_meta, dict):
                            detected_p = json_meta.get("phone") or json_meta.get("phone_number") or json_meta.get("session_file")
                            if detected_p:
                                phone = str(detected_p).replace(".session", "")

                        if phone.isdigit() and not phone.startswith("+"):
                            phone = f"+{phone}"

                        # Universal Session Normalization
                        from utils.session_patch import normalize_session_string
                        if session_data.startswith(b"SQLite format 3\x00"):
                            import base64
                            b64_str = base64.b64encode(session_data).decode("utf-8")
                            session_str = normalize_session_string(b64_str, default_api_id=API_ID)
                        else:
                            try:
                                text_content = session_data.decode("utf-8").strip()
                                session_str = normalize_session_string(text_content, default_api_id=API_ID)
                            except Exception:
                                import base64
                                b64_str = base64.b64encode(session_data).decode("utf-8")
                                session_str = normalize_session_string(b64_str, default_api_id=API_ID)

                        if not session_str or len(session_str) < 10:
                            failed += 1
                            continue

                        valid_sessions.append({
                            "country": final_country,
                            "flag": flag,
                            "price": price,
                            "year": year,
                            "phone": phone,
                            "data": session_str,
                            "status": "fresh",
                            "type": item_type
                        })

                    except Exception as e:
                        print(f"Session add error ({sf}): {e}")
                        failed += 1

                added = 0
                if valid_sessions:
                    added = await add_stock("sessions", valid_sessions)

                # Cleanup temp files
                shutil.rmtree(tmp_dir, ignore_errors=True)
                clear_session(user_id)

                await status.edit_text(
                    f"<b>✅ ZIP UPLOAD COMPLETE!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"{flag} <b>Country:</b> {final_country}\n"
                    f"💰 <b>Price:</b> ₹{price}\n"
                    f"📅 <b>Year:</b> {year}\n"
                    f"🏷️ <b>Type:</b> {item_type}\n\n"
                    f"✅ <b>Added:</b> {added} sessions\n"
                    f"❌ <b>Failed:</b> {failed} sessions\n\n"
                    f"<i>Stock is now live!</i>",
                    parse_mode=enums.ParseMode.HTML
                )

                if LOG_CHANNEL and LOG_CHANNEL != 0:
                    try:
                        await c.send_message(
                            LOG_CHANNEL,
                            f"📦 <b>ZIP STOCK ADDED</b>\n"
                            f"👤 Admin: <code>{user_id}</code>\n"
                            f"{flag} {final_country} | ₹{price} | {year} | {item_type}\n"
                            f"✅ {added} sessions added | ❌ {failed} failed",
                            parse_mode=enums.ParseMode.HTML
                        )
                    except:
                        pass

            except ValueError:
                temp = await msg.reply(
                    "❌ Price must be a number!\nExample: <code>India 150 2023</code>",
                    parse_mode=enums.ParseMode.HTML
                )
                await asyncio.sleep(3)
                await temp.delete()
            except Exception as e:
                await msg.reply(f"❌ Error: <code>{str(e)[:100]}</code>", parse_mode=enums.ParseMode.HTML)
                clear_session(user_id)

    except Exception as e:
        print(f"Master Listener Error: {e}")


@Client.on_callback_query(filters.regex("close_admin"))
async def close_admin_panel(c, cb):
    await cb.answer()
    await cb.message.delete()
    clear_session(cb.from_user.id)


# ==================================================================
# 🧨 SEARCH & DESTROY (Delete + Logout)
# ==================================================================

@Client.on_callback_query(filters.regex("search_stock_dlt"))
async def search_stock_trigger(c, cb):
    admin_session[cb.from_user.id] = {"mode": "wait_stock_search"}
    await cb.answer()
    await cb.message.edit_text(
        "<b>🔍 SEARCH STOCK TO DELETE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send the <b>Phone Number</b> of the account you want to delete.\n"
        "Example: <code>+919876543210</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="admin_stock")]]),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_message(filters.user(ADMINS) & filters.text, group=6)
async def handle_stock_search(c, msg):
    user_id = msg.from_user.id
    if user_id not in admin_session or admin_session[user_id].get("mode") != "wait_stock_search":
        return

    phone = msg.text.strip().replace(" ", "")
    item = await col_stock.find_one({"phone": {"$regex": phone}})
    
    if not item:
        return await msg.reply("❌ <b>Item Not Found!</b>\nMake sure the phone number is correct.", parse_mode=enums.ParseMode.HTML)

    clear_session(user_id)
    
    text = (
        "<b>📦 ITEM FOUND</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 <b>Country:</b> {item.get('flag','🏳️')} {item['country']}\n"
        f"📅 <b>Year:</b> {item['year']}\n"
        f"📞 <b>Phone:</b> <code>{item['phone']}</code>\n"
        f"💰 <b>Price:</b> ₹{item['price']}\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ <b>WARNING:</b> Deleting will also <b>LOG OUT</b> the session from Telegram permanently."
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🧨 DELETE & DESTROY", callback_data=f"dlt_destroy_{item['_id']}")],
        [InlineKeyboardButton("🔙 Cancel", callback_data="admin_stock")]
    ])
    
    await msg.reply(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^dlt_destroy_(.+)"))
async def delete_and_destroy_handler(c, cb):
    item_id = cb.data.replace("dlt_destroy_", "")
    item = await col_stock.find_one({"_id": item_id})
    
    if not item:
        return await cb.answer("❌ Item already deleted!", show_alert=True)

    await cb.message.edit_text("<b>🧨 Terminating Session...</b>", parse_mode=enums.ParseMode.HTML)
    
    # 1. LOGOUT FROM TELEGRAM
    session_str = item.get("data")
    if session_str:
        try:
            from config import API_ID, API_HASH
            from utils.session_patch import normalize_session_string
            from hydrogram.raw.functions.auth import LogOut
            clean_str = normalize_session_string(str(session_str), default_api_id=API_ID)
            temp_client = Client(name=":memory:", api_id=API_ID, api_hash=API_HASH, session_string=clean_str, in_memory=True)
            await temp_client.connect()
            try:
                await temp_client.invoke(LogOut())
            except Exception:
                pass
            await temp_client.disconnect()
        except Exception as e:
            print(f"Logout failed (Session likely already dead): {e}")

    # 2. DELETE FROM DB
    from database import delete_stock_item
    await delete_stock_item(item_id)
    
    await cb.message.edit_text(
        "<b>✅ SUCCESS!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📞 <b>Phone:</b> <code>{item['phone']}</code>\n"
        "🏁 <b>Status:</b> Deleted & Logged Out.\n"
        "━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Stock", callback_data="admin_stock")]]),
        parse_mode=enums.ParseMode.HTML
    )



# ==================================================================
# 💰 PRICE EDITOR (Bulk Update)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^(admin_edit_price|page_ep_\d+)$"))
async def price_editor_menu(c, cb):
    page = 1
    if cb.data.startswith("page_ep_"):
        page = int(cb.data.split("_")[-1])

    countries = await get_unique_countries()
    if not countries:
        return await cb.answer("No stock available to edit!", show_alert=True)

    items_list = [
        {"text": f"✏️ {i.get('flag', '🏳️')} {i['_id']}", "callback_data": f"list_buckets_ep_{i['_id']}"}
        for i in countries
    ]

    from utils import get_pagination_keyboard
    kb = get_pagination_keyboard(
        current_page=page,
        total_count=len(items_list),
        data_list=items_list,
        callback_prefix="page_ep",
        row_width=2
    )
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="admin_home")])

    await cb.answer()
    await cb.message.edit_text("<b>💰 SELECT COUNTRY TO EDIT PRICES:</b>", reply_markup=kb, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^list_buckets_ep_(.+)"))
async def list_buckets_for_price_edit(c, cb):
    country = cb.data.replace("list_buckets_ep_", "", 1)
    buckets = await get_buckets_by_country(country)
    
    if not buckets:
        return await cb.answer("No buckets found!", show_alert=True)

    settings = await col_settings.find_one({"_id": "main_config"}) or {}
    try:
        usdt_rate = float(settings.get("usdt_rate", 90.0))
        if usdt_rate <= 0: usdt_rate = 90.0
    except:
        usdt_rate = 90.0

    buttons = []
    for b in buckets:
        price = b['price']
        price_usdt = round(price / usdt_rate, 2)
        text = f"{b.get('flag','🏳️')} {b['year']} - ₹{price} (${price_usdt}) ({b['count']} items)"
        buttons.append([InlineKeyboardButton(text, callback_data=f"ask_np|{country}|{b['year']}|{price}")])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_edit_price")])
    
    await cb.answer()
    await cb.message.edit_text(
        f"<b>✏️ EDIT PRICES - {country.upper()}</b>\nSelect a bucket to change its price:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=enums.ParseMode.HTML
    )

@Client.on_callback_query(filters.regex(r"^ask_np\|(.+)\|(.+)\|(.+)"))
async def ask_new_price_input(c, cb):
    _, country, year, old_price = cb.data.split("|")
    
    admin_session[cb.from_user.id] = {
        "mode": "wait_new_price",
        "country": country,
        "year": year,
        "old_price": old_price,
        "msg_id": cb.message.id
    }
    
    await cb.message.edit_text(
        f"<b>✏️ EDIT PRICE: {country} ({year})</b>\n"
        f"Current Price: ₹{old_price}\n\n"
        "<b>Reply with the NEW price (numbers only):</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_edit_price")]]),
        parse_mode=enums.ParseMode.HTML
    )
    await cb.answer()

@Client.on_message(filters.user(ADMINS) & filters.text, group=5)
async def handle_price_input(c, msg):
    user_id = msg.from_user.id
    if user_id not in admin_session or admin_session[user_id].get("mode") != "wait_new_price":
        return

    state = admin_session[user_id]
    new_price_str = msg.text.strip()
    
    try: await msg.delete()
    except: pass

    if not new_price_str.isdigit():
        return await c.send_message(user_id, "❌ Please send a valid number for the price.")

    new_price = int(new_price_str)
    
    # Update in DB
    from database import update_bucket_price
    updated_count = await update_bucket_price(state["country"], state["year"], state["old_price"], new_price)
    
    clear_session(user_id)
    
    await c.send_message(
        user_id,
        f"<b>✅ PRICE UPDATED!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🌍 <b>Country:</b> {state['country']}\n"
        f"📅 <b>Year:</b> {state['year']}\n"
        f"💰 <b>New Price:</b> ₹{new_price}\n"
        f"📦 <b>Items Updated:</b> {updated_count}\n"
        f"━━━━━━━━━━━━━━━━━━━━",
        parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Admin", callback_data="admin_home")]])
    )
