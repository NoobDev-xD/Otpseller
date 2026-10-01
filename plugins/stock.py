import os
import asyncio
try:
    import pycountry  # type: ignore
except ImportError:
    pycountry = None
import re
from hydrogram import Client, filters, enums
from hydrogram.errors import (
    SessionPasswordNeeded, PhoneCodeInvalid, PhoneCodeExpired, 
    BadRequest, AuthKeyUnregistered, UserDeactivated, SessionRevoked, Unauthorized
)
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, Message
from config import ADMINS, LOG_CHANNEL, STATIC_2FA_PASSWORD, API_ID, API_HASH
from database import (
    col_stock, add_stock, get_unique_buckets, get_unique_countries,
    get_user, update_balance
)
from utils import get_pagination_keyboard, get_divider, mask_text, normalize_session_string

# ==================================================================
# 🧠 SHARED STATE 
# ==================================================================
from plugins.admin import admin_session, clear_session

# ==================================================================
# 🛠️ HELPER: SESSION VALIDATOR
# ==================================================================
async def validate_and_parse_session(session_string):
    """
    Connects to Telegram to check if session is alive.
    Returns: (is_alive, phone_number, user_obj)
    """
    clean_session = normalize_session_string(str(session_string), default_api_id=API_ID) if session_string else session_string
    temp_client = Client(
        name=":memory:",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=clean_session,
        no_updates=True,
        in_memory=True
    )
    try:
        await temp_client.connect()
        me = await temp_client.get_me()
        await temp_client.disconnect()
        if not me:
            return False, None, None
        phone = f"+{me.phone_number}" if me.phone_number and not str(me.phone_number).startswith("+") else str(me.phone_number or "Unknown")
        return True, phone, me
    except (AuthKeyUnregistered, UserDeactivated, SessionRevoked, Unauthorized):
        return False, None, None
    except Exception as e:
        print(f"Validation Error: {e}")
        return False, None, None
    finally:
        try: 
            if temp_client.is_connected: await temp_client.disconnect()
        except: pass

# ==================================================================
# 📂 1. UPLOAD SELECTOR & FORMAT ACTIVATION
# ==================================================================

@Client.on_callback_query(filters.regex(r"^pre_upload[\|_](.+)"))
async def select_upload_type(c, cb):
    try:
        raw = cb.data
        if raw.startswith("pre_upload|"):
            parts = raw.replace("pre_upload|", "", 1).split("|")
            country, price, year = parts[0], parts[1], parts[2]
            item_type = parts[3].lower().rstrip("s") if len(parts) > 3 else "account"
        else:
            remainder = raw.replace("pre_upload_", "", 1)
            parts = remainder.rsplit("_", 3)
            if len(parts) == 4:
                country, price, year, item_type = parts[0], parts[1], parts[2], parts[3].lower().rstrip("s")
            else:
                country, price, year = parts[0], parts[1], parts[2]
                item_type = "account"
        
        text = (
            f"<b>📂 UPLOADING TO: {country}</b>\n"
            f"💰 Price: ₹{price} | 📅 Year: {year} | 🏷️ Type: {item_type}\n"
            f"{get_divider()}\n"
            "<b>Select Upload Format:</b>"
        )
        base_data = f"{country}_{price}_{year}_{item_type}"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 TXT File", callback_data=f"setmode_txt_{base_data}")],
            [InlineKeyboardButton("✏️ Direct Text", callback_data=f"setmode_text_{base_data}")],
            [InlineKeyboardButton("📲 Login (Num)", callback_data=f"setmode_login_{base_data}")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_stock")]
        ])
        await cb.message.edit_text(text, parse_mode=enums.ParseMode.HTML, reply_markup=buttons)
    except Exception as e:
        await cb.answer("Error parsing data!", show_alert=True)

async def manual_activate_upload(client, message, country, price, year, flag, admin_id, item_type="account"):
    """Called by admin.py master listener after smart input."""
    item_type = item_type.lower().rstrip("s")
    text = (
        f"<b>✅ CATEGORY READY: {country}</b>\n"
        f"💰 Price: ₹{price} | 📅 Year: {year} | 🏷️ Type: {item_type}\n"
        f"{get_divider()}\n"
        "<b>Select Upload Format:</b>"
    )
    base_data = f"{country}_{price}_{year}_{item_type}"
    buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📄 TXT File", callback_data=f"setmode_txt_{base_data}")],
            [InlineKeyboardButton("✏️ Direct Text", callback_data=f"setmode_text_{base_data}")],
            [InlineKeyboardButton("📲 Login (Num)", callback_data=f"setmode_login_{base_data}")],
            [InlineKeyboardButton("🛑 Cancel", callback_data="admin_stock")]
    ])
    
    if hasattr(message, "edit_text"):
        await message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
    else:
        await client.send_message(message.chat.id, text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^setmode[\|_](.+)"))
async def activate_upload_mode(c, cb):
    try:
        raw = cb.data
        if raw.startswith("setmode|"):
            parts = raw.replace("setmode|", "", 1).split("|")
            upload_type, country, price, year = parts[0], parts[1], int(parts[2]), parts[3]
            item_type = parts[4].lower().rstrip("s") if len(parts) > 4 else "account"
        else:
            data = raw.replace("setmode_", "", 1)
            upload_type, rest = data.split("_", 1)
            parts = rest.rsplit("_", 3)
            if len(parts) == 4:
                country, price, year, item_type = parts[0], int(parts[1]), parts[2], parts[3].lower().rstrip("s")
            else:
                country, price, year = parts[0], int(parts[1]), parts[2]
                item_type = "account"
    except Exception as e:
        return await cb.answer("Error activating mode!", show_alert=True)
    
    try:
        matches = pycountry.countries.search_fuzzy(country) if pycountry else None
        flag = "".join([chr(ord(c) + 127397) for c in matches[0].alpha_2]) if matches else "🏳️"
    except:
        flag = "🏳️"

    # Initialize Session State
    admin_session[cb.from_user.id] = {
        "mode": f"uploading_{upload_type}",
        "country": country, 
        "price": price, 
        "year": year,
        "flag": flag,
        "item_type": item_type,
        "menu_id": cb.message.id, 
        "otp_buffer": "",
        "temp_client": None 
    }
    
    instructions = {
        "txt": "<b>📄 UPLOAD TXT FILE</b>\nSend a .txt file containing session strings.",
        "text": "<b>✏️ PASTE TEXT</b>\nSend session strings directly (one per line).",
        "login": "<b>📲 LOGIN FLOW</b>\nSend the Phone Number (+91...) to start."
    }

    text = (
        f"🚀 <b>MODE: {upload_type.upper()} ACTIVE</b>\n"
        f"{get_divider()}\n"
        f"🌍 Country: {flag} {country}\n"
        f"💰 Price: ₹{price}\n"
        f"{get_divider()}\n"
        f"{instructions.get(upload_type)}\n\n"
        "<i>Bot is listening... All inputs undergo Live Check.</i>"
    )
    await cb.answer()
    await cb.message.edit_text(
        text, parse_mode=enums.ParseMode.HTML,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 STOP & CANCEL", callback_data="admin_stock")]])
    )

# ==================================================================
# 📟 3. THE DIALPAD UI
# ==================================================================

def get_dialpad_markup(current_code=""):
    header = f"<code>{current_code if current_code else 'ENTER OTP'}</code>"
    line = "━━━━━━━━━━━━━━━━━━━━"
    keyboard = [
        [InlineKeyboardButton("7", callback_data="num_7"), InlineKeyboardButton("8", callback_data="num_8"), InlineKeyboardButton("9", callback_data="num_9")],
        [InlineKeyboardButton("4", callback_data="num_4"), InlineKeyboardButton("5", callback_data="num_5"), InlineKeyboardButton("6", callback_data="num_6")],
        [InlineKeyboardButton("1", callback_data="num_1"), InlineKeyboardButton("2", callback_data="num_2"), InlineKeyboardButton("3", callback_data="num_3")],
        [InlineKeyboardButton("⌫", callback_data="num_back"), InlineKeyboardButton("0", callback_data="num_0"), InlineKeyboardButton("✅ LOGIN", callback_data="num_done")]
    ]
    return header, line, InlineKeyboardMarkup(keyboard)

# ==================================================================
# 🤖 5. MASTER INPUT LISTENER (With Validation)
# ==================================================================

@Client.on_message(filters.user(ADMINS) & (filters.text | filters.document) & ~filters.command(["admin", "start"]), group=3)
async def stock_input_listener(c, msg):
    user_id = msg.from_user.id
    if user_id not in admin_session: return
    
    state = admin_session[user_id]
    mode = state.get("mode")
    
    try: await msg.delete()
    except: pass

    # ---  ---
    raw_lines = []
    if mode == "uploading_txt" and msg.document:
        path = await msg.download()
        fn = (msg.document.file_name or "").lower()
        if fn.endswith(".zip"):
            try:
                import zipfile, tempfile, shutil
                tmp_e = tempfile.mkdtemp()
                with zipfile.ZipFile(path, 'r') as z:
                    z.extractall(tmp_e)
                for root, dirs, files in os.walk(tmp_e):
                    for f in files:
                        fp = os.path.join(root, f)
                        with open(fp, "rb") as sbf:
                            sdata = sbf.read()
                        if sdata.startswith(b"SQLite format 3\x00"):
                            import base64
                            s_str = normalize_session_string(base64.b64encode(sdata).decode("utf-8"), default_api_id=API_ID)
                            if s_str:
                                p_guess = re.sub(r"\.(session|json|txt)$", "", f, flags=re.IGNORECASE)
                                raw_lines.append(f"{p_guess}:{s_str}" if p_guess.isdigit() else s_str)
                        else:
                            try:
                                for l in sdata.decode("utf-8", errors="ignore").splitlines():
                                    if len(l.strip()) > 10:
                                        raw_lines.append(l.strip())
                            except Exception:
                                pass
                shutil.rmtree(tmp_e, ignore_errors=True)
            except Exception as e:
                print(f"Zip read error: {e}")
        elif fn.endswith(".session"):
            with open(path, "rb") as f:
                sdata = f.read()
            if sdata.startswith(b"SQLite format 3\x00"):
                import base64
                s_str = normalize_session_string(base64.b64encode(sdata).decode("utf-8"), default_api_id=API_ID)
                if s_str:
                    p_guess = re.sub(r"\.(session|json|txt)$", "", msg.document.file_name, flags=re.IGNORECASE)
                    raw_lines.append(f"{p_guess}:{s_str}" if p_guess.isdigit() else s_str)
            else:
                try:
                    for l in sdata.decode("utf-8", errors="ignore").splitlines():
                        if len(l.strip()) > 10:
                            raw_lines.append(l.strip())
                except Exception:
                    pass
        else:
            with open(path, "r", errors="ignore") as f:
                raw_lines = f.readlines()
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
    elif mode == "uploading_text" and msg.text:
        raw_lines = msg.text.split("\n")

    # ---  ---
    if raw_lines:
        status_msg = await c.send_message(user_id, f"⏳ <b>Processing {len(raw_lines)} lines...</b>\n<i>Validating sessions & checking duplicates...</i>")
        
        valid_items = []
        stats = {"added": 0, "dead": 0, "duplicate": 0}
        item_type = str(state.get("item_type", "account")).lower().rstrip("s")
        
        for line in raw_lines:
            line_str = line.strip().strip('"\'')
            if len(line_str) < 10: continue
            
            # Extract session string and phone from line (handles phone:session, session:2fa, phone|session, etc.)
            session_string = line_str
            extracted_phone = None
            
            if any(sep in line_str for sep in [":", "|", "\t", " "]):
                parts = re.split(r'[:|\t\s]+', line_str)
                for part in parts:
                    clean_part = part.strip().strip('"\'')
                    if len(clean_part) > 40:
                        session_string = clean_part
                    elif re.match(r'^\+?\d{8,15}$', clean_part):
                        extracted_phone = clean_part if clean_part.startswith("+") else f"+{clean_part}"
            
            # Normalize session string (Pyrogram v1/v2, Telethon, Hydrogram)
            session_string = normalize_session_string(session_string, default_api_id=API_ID)

            # Duplicate check
            exists = await col_stock.find_one({"data": session_string, "status": "fresh"})
            if exists:
                stats["duplicate"] += 1
                continue

            # 1. LIVE CHECK
            is_alive, phone, me = await validate_and_parse_session(session_string)
            
            if not is_alive:
                stats["dead"] += 1
                continue
                
            final_phone = phone or extracted_phone or "Unknown"

            # 3. PREPARE ITEM
            valid_items.append({
                "data": session_string,
                "phone": final_phone,
                "country": state["country"],
                "flag": state["flag"], 
                "price": int(state["price"]),
                "year": str(state["year"]),
                "status": "fresh",
                "type": item_type
            })
            stats["added"] += 1
            
            # Rate limit mitigation for large files
            if len(valid_items) % 10 == 0:
                await asyncio.sleep(0.5)

        # 4. BULK INSERT
        if valid_items:
            await add_stock("sessions", valid_items)
            
        await status_msg.edit_text(
            f"<b>✅ UPLOAD REPORT</b>\n"
            f"{get_divider()}\n"
            f"🟢 <b>Added:</b> {stats['added']}\n"
            f"🔴 <b>Dead:</b> {stats['dead']}\n"
            f"⚠️ <b>Duplicate:</b> {stats['duplicate']}\n"
            f"{get_divider()}",
            parse_mode=enums.ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📦 Stock Menu", callback_data="admin_stock")]])
        )
        clear_session(user_id)
        return

    # ---  ---
    if mode == "uploading_login" and msg.text:
        phone = msg.text.strip().replace(" ", "")
        if not phone.startswith("+"):
            phone = "+" + phone
        
        status_msg = await c.send_message(user_id, f"🔄 <b>Connecting...</b>\nPhone: {phone}")
        
        temp_client = Client(name=":memory:", api_id=API_ID, api_hash=API_HASH, in_memory=True)
        
        try:
            await temp_client.connect()
        except Exception as e:
            return await status_msg.edit_text(f"❌ Connection Failed: {e}")

        try:
            sent_code = await temp_client.send_code(phone)
            admin_session[user_id].update({
                "phone": phone, 
                "mode": "login_otp_wait",
                "temp_client": temp_client,
                "phone_code_hash": sent_code.phone_code_hash,
                "otp_buffer": "" # number
            })
            
            await status_msg.delete()
            header, line, kb = get_dialpad_markup("")
            await c.edit_message_text(
                user_id, state["menu_id"], 
                f"<b>📲 LOGIN: {phone}</b>\n{line}\n{header}\n{line}\nTelegram se code dekh ke dial karo:", 
                reply_markup=kb, parse_mode=enums.ParseMode.HTML
            )
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Send Code Error: {e}")
            await temp_client.disconnect()

    # --- REAL LOGIN: STEP 3 (2FA Password Input) ---
    elif mode == "login_2fa_wait" and msg.text:
        password = msg.text
        temp_client = state["temp_client"]
        
        status_msg = await c.send_message(user_id, "🔐 Verifying Password...")
        try:
            await temp_client.check_password(password)
            
            #  DEAD SESSION CHECK (Health Check)
            me = await temp_client.get_me()
            if not me: raise Exception("Session created but dead.")

            session_string = await temp_client.export_session_string()
            await temp_client.disconnect()
            
            # Save with Flag
            item_type = str(state.get("item_type", "account")).lower().rstrip("s")
            stock_item = [{
                "data": session_string,
                "phone": state["phone"],
                "country": state["country"],
                "flag": state["flag"], 
                "price": int(state["price"]),
                "year": str(state["year"]),
                "status": "fresh",
                "type": item_type
            }]
            await add_stock("sessions", stock_item)
            
            await status_msg.edit_text(f"✅ <b>Login Successful!</b>\nSaved: {me.first_name} ({me.id})")
            
            # Reset UI
            admin_session[user_id]["mode"] = "uploading_login"
            admin_session[user_id]["temp_client"] = None
            admin_session[user_id]["otp_buffer"] = "" # 🔥 FIX: Clear buffer after 2FA success
            
            await c.edit_message_text(
                user_id, state["menu_id"], 
                "<b>📲 NEXT LOGIN</b>\nSend next Phone Number (+91...)",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop", callback_data="admin_stock")]])
            )
            
        except Exception as e:
            await status_msg.edit_text(f"❌ Password/Health Error: {e}")


# ==================================================================
# 🔢 6. DIALPAD HANDLER (With REAL Sign-In & Checks)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^num_(.+)"))
async def handle_dialpad(c, cb):
    user_id = cb.from_user.id
    if user_id not in admin_session: return await cb.answer("Session Expired!")
    
    action = cb.data.split("_")[1]
    state = admin_session[user_id]
    buffer = state.get("otp_buffer", "")
    temp_client = state.get("temp_client")

    if action.isdigit():
        if len(buffer) < 5: buffer += action
    elif action == "back":
        buffer = buffer[:-1]
    
    elif action == "done":
        if not temp_client: return await cb.answer("❌ Connection Lost. Restart.", show_alert=True)
        if len(buffer) < 5: return await cb.answer("⚠️ OTP must be 5 digits!", show_alert=True)
            
        await cb.answer("⏳ Logging in...", show_alert=False)
        await cb.message.edit_text("🔄 <b>Verifying OTP...</b>")
        
        try:
            await temp_client.sign_in(state["phone"], state["phone_code_hash"], buffer)
            
            # DEAD SESSION CHECK (Before Saving)
            try:
                me = await temp_client.get_me()
            except:
                raise Exception("Login passed but account restricted/dead.")

            session_string = await temp_client.export_session_string()
            await temp_client.disconnect()
            
            item_type = str(state.get("item_type", "account")).lower().rstrip("s")
            stock_item = [{
                "data": session_string,
                "phone": state["phone"],
                "country": state["country"],
                "flag": state["flag"], 
                "price": int(state["price"]),
                "year": str(state["year"]),
                "status": "fresh",
                "type": item_type
            }]
            await add_stock("sessions", stock_item)
            
            await cb.message.edit_text(
                f"✅ <b>LOGIN SUCCESS!</b>\nSaved: {me.first_name} ({me.id})\n\nReady for next...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🛑 Stop", callback_data="admin_stock")]])
            )
            
            
            admin_session[user_id]["mode"] = "uploading_login"
            admin_session[user_id]["temp_client"] = None
            admin_session[user_id]["otp_buffer"] = "" 
            return

        except SessionPasswordNeeded:
            admin_session[user_id]["mode"] = "login_2fa_wait"
            admin_session[user_id]["otp_buffer"] = "" 
            await cb.message.edit_text(
                "🔐 <b>TWO-STEP VERIFICATION DETECTED</b>\n\n"
                "Please send your <b>2FA Password</b> here in chat.\n"
                "<i>(Message will be auto-deleted)</i>",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_stock")]])
            )
            return

        except (PhoneCodeInvalid, PhoneCodeExpired):
            await cb.message.edit_text("❌ <b>Wrong OTP!</b>\nTry again.", reply_markup=get_dialpad_markup("")[2])
            admin_session[user_id]["otp_buffer"] = "" # Clear buffer for retry
            return
            
        except Exception as e:
            await cb.message.edit_text(f"❌ Error: {e}")
            if temp_client.is_connected: await temp_client.disconnect()
            return

    admin_session[user_id]["otp_buffer"] = buffer
    header, line, kb = get_dialpad_markup(buffer)
    try:
        await cb.message.edit_text(
            f"<b>📲 LOGIN: {state['phone']}</b>\n{line}\n{header}\n{line}\nTelegram se code dekh k dial karo:", 
            reply_markup=kb, parse_mode=enums.ParseMode.HTML
        )
    except: pass

# ==================================================================
# 🗑️ CLEAR STOCK HANDLERS  Pagination & Extraction)
# ==================================================================


@Client.on_callback_query(filters.regex(r"^page_del_\d+$"))
async def clear_stock_menu(c, cb):
    # Fetch page number dynamically
    page = 1
    if cb.data.startswith("page_del_"):
        page = int(cb.data.split("_")[-1])

    countries = await get_unique_countries()
    if not countries: 
        return await cb.answer("Stock is already empty!", show_alert=True)
    
    # Format items
    items_list = [
        {"text": f"🗑 {i.get('flag', '🏳️')} {i['_id']}", "callback_data": f"conf_del_{i['_id']}"} 
        for i in countries
    ]
    
    # Generate Keyboard
    kb = get_pagination_keyboard(
        current_page=page, 
        total_count=len(items_list), 
        data_list=items_list, 
        callback_prefix="page_del", 
        row_width=2
    )
    
    text = "<b>🗑 SELECT COUNTRY TO CLEAR:</b>"
    try:
        await cb.answer() # Stops loading spinner
        await cb.message.edit_text(text, reply_markup=kb, parse_mode=enums.ParseMode.HTML)
    except: pass

@Client.on_callback_query(filters.regex(r"^conf_del_(.+)"))
async def confirm_delete(c, cb):
    
    country = cb.data.replace("conf_del_", "")
    
    text = f"<b>⚠️ WARNING!</b>\n\nDelete ALL fresh stock for <b>{country}</b>?"
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ YES", callback_data=f"exec_del_{country}")],
        [InlineKeyboardButton("🔙 NO", callback_data="admin_delete_menu")]
    ])
    await cb.answer()
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex(r"^exec_del_(.+)"))
async def execute_clear_stock(c, cb):
    # extraction for execution
    country = cb.data.replace("exec_del_", "")
    
    res = await col_stock.delete_many({"country": country, "status": "fresh"})
    await cb.answer(f"🗑 Deleted {res.deleted_count} items from {country}!", show_alert=True)
    
    # Reload page 1 after deletion
    cb.data = "admin_delete_menu"
    await clear_stock_menu(c, cb)

