import asyncio
import re
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from hydrogram.errors import (
    SessionPasswordNeeded, AuthKeyUnregistered, 
    UserDeactivated, SessionRevoked, UserRestricted,
    MessageNotModified, Unauthorized, FloodWait
)

from hydrogram.raw.functions.account import GetAuthorizations, ResetAuthorization
from hydrogram.raw.functions.auth import LogOut  # ✅ NEW IMPORT
from config import API_ID, API_HASH, STATIC_2FA_PASSWORD
from database import col_orders, col_stock
from utils.session_patch import normalize_session_string



# ==================================================================
# 🔧 HELPER: Get Session String from Order
# ==================================================================

async def get_session_from_order(order):
    session_string = order.get("data")
    phone_val = order.get("phone", "")

    if not session_string and phone_val and len(str(phone_val)) > 50:
        session_string = phone_val

    if not session_string:
        item_id = order.get("item_id")
        if item_id:
            try:
                from bson import ObjectId
                stock = await col_stock.find_one({"_id": ObjectId(item_id)})
            except:
                stock = await col_stock.find_one({"_id": item_id})
            if stock:
                session_string = stock.get("data") or stock.get("phone")

    if session_string:
        session_string = normalize_session_string(str(session_string), default_api_id=API_ID)

    return session_string


async def fetch_order(order_id):
    try:
        from bson import ObjectId
        order = await col_orders.find_one({"_id": ObjectId(order_id)})
        if not order:
            order = await col_orders.find_one({"_id": order_id})
    except:
        order = await col_orders.find_one({"_id": order_id})
    return order


import logging
from datetime import datetime

logger = logging.getLogger(__name__)

def extract_telegram_otp(text: str) -> str | None:
    """Extracts 5 or 6 digit Telegram login OTP from message text."""
    if not text:
        return None
    # 1. Look for explicit login/code keywords
    m = re.search(r'(?:code|login|telegram|passcode|код|código|verification|auth)[^\d\n]*(\d{3}[-\s]?\d{2,3}|\d{5,6})', text, re.IGNORECASE)
    if m:
        digits = re.sub(r'\D', '', m.group(1))
        if 5 <= len(digits) <= 6:
            return digits
    # 2. Look for standalone 5 or 6 digit numbers
    m2 = re.search(r'\b(\d{5,6})\b', text)
    if m2:
        return m2.group(1)
    # 3. Look for hyphenated/spaced 5-6 digit numbers like 123-45
    m3 = re.search(r'\b(\d{3}[-\s]\d{2,3})\b', text)
    if m3:
        digits = re.sub(r'\D', '', m3.group(1))
        if 5 <= len(digits) <= 6:
            return digits
    return None


def make_temp_client(session_string):
    clean_session = normalize_session_string(str(session_string), default_api_id=API_ID) if session_string else session_string
    return Client(
        name=":memory:",
        api_id=API_ID,
        api_hash=API_HASH,
        session_string=clean_session,
        in_memory=True,
        no_updates=True,
        workers=2,
        sleep_threshold=5
    )


# ==================================================================
# 🔄 1. GET OTP HANDLER 
# ==================================================================

@Client.on_callback_query(filters.regex(r"^otp_([a-zA-Z0-9-]+)"))
async def get_otp_handler(c, cb):
    order_id = cb.data.replace("otp_", "", 1)
    
    order = await fetch_order(order_id)
        
    if not order:
        return await cb.message.edit_text(
            "❌ <b>Order not found!</b>\nIt may have expired or been deleted.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]])
        )

    session_string = await get_session_from_order(order)
    phone_number = order.get("phone", "Unknown")

    if not session_string:
        return await cb.message.edit_text(
            "❌ <b>Session Missing!</b>\nContact Admin for support.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]])
        )

    await cb.answer("🔍 Checking Telegram for OTP...", show_alert=False)

    now_str = datetime.now().strftime("%I:%M:%S %p")
    temp_client = make_temp_client(session_string)

    try:
        # Connect with 25-second timeout to accommodate MTProto handshake on remote DCs
        await asyncio.wait_for(temp_client.connect(), timeout=25.0)
        
        # Check authorization
        me = await temp_client.get_me()
        if not me:
            raise AuthKeyUnregistered()
        
        otp_code = None
        raw_msg_snippet = None

        # Priority 1: Direct 777000 chat history (Official Telegram Service)
        try:
            async for msg in temp_client.get_chat_history(777000, limit=5):
                text_content = msg.text or msg.caption or ""
                if text_content:
                    code = extract_telegram_otp(text_content)
                    if code:
                        otp_code = code
                        break
                    elif not raw_msg_snippet:
                        raw_msg_snippet = text_content[:100]
        except Exception as e:
            logger.debug(f"Direct 777000 check: {e}")

        # Priority 2: Check active dialogs for service notifications
        if not otp_code:
            try:
                async for dialog in temp_client.get_dialogs(limit=10):
                    cid = dialog.chat.id
                    cname = (dialog.chat.first_name or dialog.chat.title or "").lower()
                    if cid in (777000, 42777) or "telegram" in cname or "service" in cname:
                        async for msg in temp_client.get_chat_history(cid, limit=5):
                            text_content = msg.text or msg.caption or ""
                            if text_content:
                                code = extract_telegram_otp(text_content)
                                if code:
                                    otp_code = code
                                    break
                                elif not raw_msg_snippet:
                                    raw_msg_snippet = text_content[:100]
                    if otp_code:
                        break
            except Exception as e:
                logger.debug(f"Dialog check: {e}")

        if temp_client.is_connected:
            await temp_client.disconnect()

        if otp_code:
            text = (
                f"<b>📲 LOGIN ASSISTANT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
                f"📩 <b>OTP Code:</b> <code>{otp_code}</code> <i>(Tap to copy)</i>\n"
                f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"🕒 <b>Fetched At:</b> <code>{now_str}</code>\n"
                "✅ <i>Enter this code in Telegram to complete login.</i>"
            )
        else:
            text = (
                f"<b>📲 LOGIN ASSISTANT</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 <b>Number:</b> <code>{phone_number}</code>\n"
                f"🔐 <b>2FA Password:</b> <code>{STATIC_2FA_PASSWORD}</code>\n"
                f"⚠️ <b>Status:</b> No OTP code received yet.\n"
                f"🕒 <b>Last checked:</b> <code>{now_str}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                "<b>👇 How to receive code:</b>\n"
                f"1. Open official Telegram app & enter <code>{phone_number}</code>.\n"
                "2. Wait 5-10 seconds for Telegram to send the code.\n"
                "3. Tap <b>🔄 Refresh Code</b> below."
            )

        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Refresh Code", callback_data=f"otp_{order_id}")],
            [InlineKeyboardButton("📥 Download Session", callback_data=f"dl_session_{order_id}"), InlineKeyboardButton("📱 Manage Logins", callback_data=f"mng_{order_id}")],
            [InlineKeyboardButton("✅ Done", callback_data=f"finish_order_{order_id}")]
        ])

        try:
            await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass

    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated, Unauthorized):
        await cb.message.edit_text(
            f"<b>❌ SESSION INACTIVE / EXPIRED</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📞 Number: <code>{phone_number}</code>\n"
            "This session is logged out or revoked.\n"
            f"Please contact support with Order ID: <code>{order_id}</code>.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back to Orders", callback_data="my_orders_list")],
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]),
            parse_mode=enums.ParseMode.HTML
        )
    except FloodWait as e:
        logger.warning(f"OTP Fetch FloodWait ({order_id}): {e.value}s")
        try:
            await cb.message.edit_text(
                f"<b>⏳ Rate Limited by Telegram</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 Number: <code>{phone_number}</code>\n"
                f"Telegram requires a wait of <b>{e.value} seconds</b> before re-checking.\n"
                "Please tap Refresh Code after waiting.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh Code", callback_data=f"otp_{order_id}")],
                    [InlineKeyboardButton("🔙 Back to Orders", callback_data="my_orders_list")]
                ]),
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass
    except asyncio.TimeoutError:
        logger.warning(f"OTP Fetch Timeout ({order_id})")
        try:
            await cb.message.edit_text(
                f"<b>⚠️ Connection Timeout</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 Number: <code>{phone_number}</code>\n"
                f"🕒 Checked: <code>{now_str}</code>\n\n"
                "<i>Telegram servers took longer than expected to connect. Please tap Refresh Code again.</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh Code", callback_data=f"otp_{order_id}")],
                    [InlineKeyboardButton("🔙 Back to Orders", callback_data="my_orders_list")]
                ]),
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"OTP Fetch Error ({order_id}): {e}")
        try:
            await cb.message.edit_text(
                f"<b>⚠️ Connection Issue</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📞 Number: <code>{phone_number}</code>\n"
                f"🕒 Checked: <code>{now_str}</code>\n\n"
                f"<i>Telegram server response delayed. Please tap Refresh Code again.</i>",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh Code", callback_data=f"otp_{order_id}")],
                    [InlineKeyboardButton("🔙 Back to Orders", callback_data="my_orders_list")]
                ]),
                parse_mode=enums.ParseMode.HTML
            )
        except Exception:
            pass

    finally:
        try:
            if temp_client.is_connected:
                await temp_client.disconnect()
        except Exception:
            pass


# ==================================================================
# 📱 2. MANAGE LOGINS (List & Terminate)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^mng_([a-zA-Z0-9-]+)"))
async def manage_sessions_handler(c, cb):
    order_id = cb.data.replace("mng_", "", 1)
    order = await fetch_order(order_id)
    if not order:
        return await cb.message.edit_text("❌ <b>Order missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    session_string = await get_session_from_order(order)
    if not session_string:
        return await cb.message.edit_text("❌ <b>Session Missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    await cb.message.edit_text("🔄 <b>Fetching Devices...</b>", parse_mode=enums.ParseMode.HTML)

    temp_client = make_temp_client(session_string)

    try:
        await asyncio.wait_for(temp_client.connect(), timeout=25.0)
        
        auths = await temp_client.invoke(GetAuthorizations())
        
        buttons = []
        for a in auths.authorizations:
            if a.current:
                # ✅ NOW CLICKABLE — user can logout bot session too
                buttons.append([InlineKeyboardButton(
                    f"🟢 THIS BOT ({a.app_name}) | ❌ Logout",
                    callback_data=f"kill_current_{order_id}_{a.hash}"
                )])
            else:
                buttons.append([InlineKeyboardButton(
                    f"📱 {a.device_model} ({a.app_name}) | ❌ Logout",
                    callback_data=f"kill_{order_id}_{a.hash}"
                )])
        
        if len(buttons) == 0:
            buttons.append([InlineKeyboardButton("✅ No active sessions found", callback_data="ignore")])

        buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"otp_{order_id}")])
        
        await cb.message.edit_text(
            f"<b>📱 ACTIVE SESSIONS ({len(auths.authorizations)})</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Tap a device to force logout (kill session).</i>",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=enums.ParseMode.HTML
        )
        
        await temp_client.disconnect()

    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated, Unauthorized):
        await cb.message.edit_text(
            "<b>❌ SESSION DEAD</b>\nCannot fetch devices.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="home")]]),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await cb.message.edit_text(
            f"❌ Error: {e}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="home")]]),
            parse_mode=enums.ParseMode.HTML
        )
    finally:
        if temp_client.is_connected:
            await temp_client.disconnect()


# ==================================================================
# 🔴 3. KILL SESSION (Terminate Other Device)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^kill_(.+)_(-?\d+)$"))
async def kill_session_handler(c, cb):
    order_id = cb.data.replace("kill_", "").rsplit("_", 1)[0]
    session_hash = int(cb.data.rsplit("_", 1)[1])

    order = await fetch_order(order_id)

    if not order:
        return await cb.message.edit_text("❌ <b>Order missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    session_string = await get_session_from_order(order)
    if not session_string:
        return await cb.message.edit_text("❌ <b>Session Missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    await cb.answer("⚡ Terminating Session...", show_alert=False)
    
    temp_client = make_temp_client(session_string)
    
    try:
        await asyncio.wait_for(temp_client.connect(), timeout=25.0)
        await temp_client.invoke(ResetAuthorization(hash=session_hash))
        await cb.answer("✅ Device Logged Out!", show_alert=True)

        # Refresh list automatically
        cb.data = f"mng_{order_id}"
        await manage_sessions_handler(c, cb)
        
    except Exception as e:
        await cb.answer(f"❌ Failed: {e}", show_alert=True)
    finally:
        if temp_client.is_connected:
            await temp_client.disconnect()


# ==================================================================
# 🔴 4. KILL CURRENT — Show Confirm Screen
# ==================================================================

@Client.on_callback_query(filters.regex(r"^kill_current_(.+)_(-?\d+)$"))
async def kill_current_session_handler(c, cb):
    order_id = cb.data.replace("kill_current_", "").rsplit("_", 1)[0]
    session_hash = int(cb.data.rsplit("_", 1)[1])

    order = await fetch_order(order_id)

    if not order:
        return await cb.message.edit_text("❌ <b>Order not found!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    session_string = await get_session_from_order(order)
    if not session_string:
        return await cb.message.edit_text("❌ <b>Session Missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    # Show confirm screen
    await cb.message.edit_text(
        "<b>⚠️ CONFIRM LOGOUT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "You are about to logout <b>THIS BOT's session</b> from the account.\n\n"
        "After this, the bot will no longer be connected to that account.\n"
        "Are you sure?",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Yes, Logout", callback_data=f"confirm_kill_current_{order_id}_{session_hash}"),
                InlineKeyboardButton("❌ Cancel", callback_data=f"mng_{order_id}")
            ]
        ]),
        parse_mode=enums.ParseMode.HTML
    )


# ==================================================================
# 🔴 5. CONFIRM KILL CURRENT — Actually Logout Using LogOut()
# ==================================================================

@Client.on_callback_query(filters.regex(r"^confirm_kill_current_(.+)_(-?\d+)$"))
async def confirm_kill_current_handler(c, cb):
    order_id = cb.data.replace("confirm_kill_current_", "").rsplit("_", 1)[0]
    session_hash = int(cb.data.rsplit("_", 1)[1])

    order = await fetch_order(order_id)

    if not order:
        return await cb.message.edit_text("❌ <b>Order missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    session_string = await get_session_from_order(order)
    if not session_string:
        return await cb.message.edit_text("❌ <b>Session Missing!</b>", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]))

    await cb.answer("⚡ Logging out bot session...", show_alert=False)

    temp_client = make_temp_client(session_string)

    try:
        await asyncio.wait_for(temp_client.connect(), timeout=25.0)

        # ✅ KEY FIX: Use LogOut() instead of ResetAuthorization()
        # ResetAuthorization cannot kill its own current session
        # LogOut() properly terminates the current active session
        await temp_client.invoke(LogOut())

        # ✅ Show success — LogOut() worked
        await cb.message.edit_text(
            "<b>✅ BOT SESSION LOGGED OUT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "The bot has been successfully logged out from this account.\n\n"
            "<i>The account is now free from this bot session.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]),
            parse_mode=enums.ParseMode.HTML
        )

    except (AuthKeyUnregistered, SessionRevoked, UserDeactivated, Unauthorized):
        # ✅ These errors are EXPECTED after LogOut() — treat as success
        await cb.message.edit_text(
            "<b>✅ BOT SESSION LOGGED OUT</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "The bot has been successfully logged out from this account.\n\n"
            "<i>The account is now free from this bot session.</i>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🏠 Home", callback_data="home")]
            ]),
            parse_mode=enums.ParseMode.HTML
        )
    except Exception as e:
        await cb.answer(f"❌ Failed: {str(e)[:80]}", show_alert=True)
    finally:
        try:
            if temp_client.is_connected:
                await temp_client.disconnect()
        except:
            pass  # ✅ Ignore stop errors after logout — connection already dead


# ==================================================================
# ✅ 6. ORDER FINISH / THANK YOU SCREEN
# ==================================================================

@Client.on_callback_query(filters.regex(r"^finish_order_([a-zA-Z0-9-]+)"))
async def finish_order_summary(c, cb):
    order_id = cb.data.replace("finish_order_", "", 1)
    
    try:
        from bson import ObjectId
        order = await col_orders.find_one({"_id": ObjectId(order_id)})
        if not order: order = await col_orders.find_one({"_id": order_id})
    except:
        order = await col_orders.find_one({"_id": order_id})
        
    if not order:
        return await cb.message.edit_text(
            "✅ <b>Thank You!</b>",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="home")]]),
            parse_mode=enums.ParseMode.HTML
        )

    item_name = f"{order.get('flag', '🏳️')} {order.get('country', 'Unknown')}"
    price = order.get('price', 0)
    phone = order.get('phone', 'Hidden')
    
    text = (
        "<b>🎉 PURCHASE COMPLETED!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 <b>Item:</b> {item_name}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"📞 <b>Phone:</b> <code>{phone}</code>\n"
        f"🆔 <b>Order ID:</b> <code>{order_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Thank you for shopping with us!</i>\n"
        "<i>If you face any issues, contact support.</i>"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🛍 Buy More", callback_data="home")],
        [InlineKeyboardButton("📞 Support", callback_data="help")]
    ])
    
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)


# ==================================================================
# 🚫 7. SUPPORT & IGNORE CALLBACKS
# ==================================================================

@Client.on_callback_query(filters.regex("^help$"))
async def help_callback(c, cb):
    await cb.answer()
    text = (
        "📞 <b>CUSTOMER SUPPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👤 <b>Contact Support:</b> @GOOD_BOY_BANNY1433\n\n"
        "<i>• Send Payment Proofs\n• Report Login Issues\n• Bulk Orders</i>"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Back", callback_data="home")]
    ])
    await cb.message.edit_text(text, reply_markup=buttons, parse_mode=enums.ParseMode.HTML)

@Client.on_callback_query(filters.regex("^ignore_dev$"))
async def ignore_callback(c, cb):
    await cb.answer("⚠️ You cannot logout the bot itself!", show_alert=True)


@Client.on_callback_query(filters.regex("^ignore$"))
async def silent_ignore(c, cb):
    await cb.answer()