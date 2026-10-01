import datetime
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, CallbackQuery, Message
from database import add_user, get_user, get_fsub_list, get_maintenance, db, col_users, set_referrer, col_orders, col_payments, toggle_terms, get_live_usdt_rate
from utils import format_price, get_divider, get_pagination_keyboard
from config import DEFAULT_FSUB_ID, DEFAULT_FSUB_LINK, ADMINS

# ==================================================================
# 🚦 CONFIG
# ==================================================================
MAIN_BUTTONS = [
    "📱 Buy Accounts", "📂 Buy Sessions", 
    "👛 Add Funds", "👤 My Profile", 
    "💰 Earn Money", "📞 Support", "📖 How to Use"
]

# ==================================================================
# 🧠 LOGIC
# ==================================================================

async def check_fsub_status(client, user_id):
    """
    Checks all FSub channels and returns a list of missing ones.
    """
    if user_id in ADMINS: return True, []
        
    fsubs = await get_fsub_list()
    if not fsubs: return True, []
    
    missing_channels = []
    for f in fsubs:
        try:
            await client.get_chat_member(f['_id'], user_id)
        except:
            missing_channels.append(f) 
            
    if not missing_channels:
        return True, []
    return False, missing_channels

# ==================================================================
# 🛠️ HELPER: SAFE SEND
# ==================================================================
async def safe_send(message_or_callback, text, reply_markup):
    try:
        if isinstance(message_or_callback, CallbackQuery):
            try: await message_or_callback.answer()
            except: pass
            await message_or_callback.message.edit_text(text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)
        elif isinstance(message_or_callback, Message):
            if message_or_callback.outgoing: 
                await message_or_callback.edit_text(text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)
            else:
                await message_or_callback.reply_text(text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            return
        if hasattr(message_or_callback, "message"):
             await message_or_callback.message.reply_text(text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)
        else:
             await message_or_callback.reply_text(text, reply_markup=reply_markup, parse_mode=enums.ParseMode.HTML)

# ==================================================================
# 🏠 UI HELPERS
# ==================================================================

async def show_terms(client, message):
    text = (
        "<b>⚠️ TERMS AND CONDITIONS</b>\n"
        f"{get_divider()}\n"
        "Please read and accept our terms to use this bot:\n\n"
        "📍 <b>Account Policy:</b>\n"
        "• These accounts are for testing/educational purposes.\n"
        "• We are <b>NOT</b> responsible for any ban/freeze after login.\n"
        "• Use <b>Telegram X</b> or official apps for best stability.\n\n"
        "💰 <b>Refund Policy:</b>\n"
        "• <b>NO REFUNDS</b> under any circumstances except 'No OTP Received'.\n"
        "• All sales are final. Buy at your own risk.\n\n"
        "🚫 <b>For better understanding:</b> <a href='https://t.me/aptrule'>read this</a>\n\n"
        "<i>By clicking 'Accept', you agree to all the terms.</i>"
    )
    
    # Ye lines function ke andar honi chahiye (aage spaces dekho)
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I Accept & Agree", callback_data="accept_terms")],
        [InlineKeyboardButton("❌ Decline", callback_data="decline_terms")]
    ])
    
    await safe_send(message, text, buttons)


async def show_fsub(client, message, missing_channels):
    """
    Shows all missing FSub channels as buttons.
    """
    text = (
        "<b>📢 JOIN OUR CHANNELS</b>\n"
        f"{get_divider()}\n"
        "You must be a member of all our update channels to access the store.\n\n"
        "👇 <b>Join all channels below and click Verify.</b>"
    )
    
    buttons = []
    # Dynamically create buttons for each missing channel
    for channel in missing_channels:
        btn_text = f"📢 Join {channel.get('title', 'Channel')}"
        buttons.append([InlineKeyboardButton(btn_text, url=channel.get('link', DEFAULT_FSUB_LINK))])
    
    # Add Verify Button 
    buttons.append([InlineKeyboardButton("✅ Verify Membership", callback_data="verify_fsub")])
    
    await safe_send(message, text, InlineKeyboardMarkup(buttons))
async def show_main_menu(client, message):
    # 1. Reply Keyboard
    reply_kb = ReplyKeyboardMarkup(
        [
            ["📱 Buy Accounts", "📂 Buy Sessions"],
            ["👛 Add Funds", "👤 My Profile"],
            ["💰 Earn Money", "📞 Support"],
            ["📖 How to Use"]
        ],
        resize_keyboard=True
    )

    # 2. User Mention
    user_mention = f"<a href='tg://user?id={message.from_user.id}'>{message.from_user.first_name}</a>"

    # 3. Text
    Text = (
        f"Hey, {user_mention} ⚡🦁\n\nWelcome to Telegram Account Seller 🥂\n\n📱 Premium Telegram Accounts
⚡ Fast & Easy Delivery
🔐 Secure & Reliable Service\n\n━━━━━━━━━━━━━━━━━━━━\n\n🛒 To order, select an account below and follow the payment instructions.
💬 If you face any issues, feel free to contact me anytime. href='https://t.me/RudraNoxx Click Here<a/>"
    )  
    # 4. Get message object
    if isinstance(message, CallbackQuery):
        try: await message.answer()
        except: pass
        msg_obj = message.message
    else:
        msg_obj = message

    # 5. Delete old message
    try:
        await msg_obj.delete()
    except:
        pass

    # 6. Send new message
    await client.send_message(
        msg_obj.chat.id,
        Text,
        reply_markup=reply_kb,
        parse_mode=enums.ParseMode.HTML,
        disable_web_page_preview=True
    )
# ==================================================================
# 🚦 STEP 2: HANDLERS
# ==================================================================

@Client.on_message(filters.private, group=-1)
async def security_middleware(c, msg):
    from utils.protection import is_flooding, is_dangerous_input
    
    user_id = msg.from_user.id
    
    # 1. Anti-Flood Check
    if await is_flooding(user_id):
        await msg.reply_text("⚠️ <b>ANTI-SPAM ACTIVE</b>\n\nYou are sending messages too fast! Please wait 15 seconds.")
        msg.stop_propagation()
        return

    # 2. Dangerous Payload Check
    if is_dangerous_input(msg.text):
        await msg.reply_text("🛑 <b>DANGEROUS INPUT DETECTED</b>\n\nYour message contains restricted characters or commands. This attempt has been logged.")
        msg.stop_propagation()
        return

@Client.on_message(filters.command("start") & filters.private)
async def start_handler(c, msg):
    user_id = msg.from_user.id
    
    # 🔥Maintenance Check
    if await get_maintenance() and user_id not in ADMINS:
        return await msg.reply_text("🚧 <b>BOT UNDER MAINTENANCE</b>\n\nAbhi updates chal rahe hain, thodi der baad try karein.")

    await add_user(user_id, msg.from_user.first_name)
    
    # REFERRAL TRACKING START
    if len(msg.command) > 1 and "ref_" in msg.text:
        try:
            referrer_id = int(msg.command[1].split("_")[1])
            if referrer_id != user_id:
  
                await set_referrer(user_id, referrer_id)
        except: pass


    # Admin Direct Access
    if user_id in ADMINS:
        return await show_main_menu(c, msg)

    # Standard Checks
    user = await get_user(user_id)
    if not user.get("terms_accepted"):
        return await show_terms(c, msg)
    
    is_joined, missing_channels = await check_fsub_status(c, user_id)
    if not is_joined:
        return await show_fsub(c, msg, missing_channels)

    
    await show_main_menu(c, msg)

@Client.on_message(filters.text & filters.private, group=1)
async def handle_reply_text(c, msg):
    if msg.text.startswith("/"):
        msg.continue_propagation()
        return

    if msg.text not in MAIN_BUTTONS:
        msg.continue_propagation()
        return

    user_id = msg.from_user.id
    
    if await get_maintenance() and user_id not in ADMINS:
        return await msg.reply_text("🚧 Maintenance Mode ON")

    btn_text = msg.text

    # Admin Bypass Logic
    if user_id not in ADMINS:
        user = await get_user(user_id)
        if not user or not user.get("terms_accepted"):
            return await show_terms(c, msg)
        
        is_joined, link = await check_fsub_status(c, user_id)
        if not is_joined:
            return await show_fsub(c, msg, link)

    # --- BUTTON HANDLERS ---
    
    if btn_text == "📱 Buy Accounts":
        try:
            from plugins.buy import show_category_list 
            msg.data = "cat_accounts" 
            await show_category_list(c, msg, category="accounts")
        except ImportError:
            await msg.reply_text("❌ Error: Buy plugin not found.")

    elif btn_text == "📂 Buy Sessions":
        try:
            from plugins.buy import show_category_list
            msg.data = "cat_sessions"
            await show_category_list(c, msg, category="sessions")
        except ImportError:
            await msg.reply_text("❌ Error: Buy plugin not found.")

    elif btn_text == "👛 Add Funds":
        try:
            from plugins.deposit import safe_deposit_menu 
            await safe_deposit_menu(c, msg)
        except ImportError:
             await msg.reply_text("❌ Error: Deposit plugin missing.")

    elif btn_text == "👤 My Profile":
        await show_profile_ui(c, msg)

    # 🔥 NEW: EARN MONEY & REFERRAL
    elif btn_text == "💰 Earn Money":
        bot_usr = (await c.get_me()).username
        ref_link = f"https://t.me/{bot_usr}?start=ref_{user_id}"
        
        text = (
            "<b>💰 EARN MONEY & REWARDS</b>\n"
            f"{get_divider()}\n"
            "Invite friends and earn bonus balance!\n\n"
            "<b>💸 How it works?</b>\n"
            "1. Share your link with friends.\n"
            "2. When your friend deposits total <b>₹1000</b>.\n"
            "3. You instantly get <b>₹20 Bonus!</b>\n\n"
            "🔗 <b>Your Referral Link:</b>\n"
            f"<code>{ref_link}</code>\n\n"
            "🎁 <b>Have a Coupon?</b>\n"
            "Use <code>/redeem CODE</code> to claim rewards."
        )
        await msg.reply_text(text, parse_mode=enums.ParseMode.HTML)

    elif btn_text == "📞 Support":
        await msg.reply_text(
            "📞 <b>Customer Support:</b> @GOOD_BOY_BANNY1433\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>• Send Payment Proofs\n• Report Login Issues\n• Bulk Orders</i>",
            parse_mode=enums.ParseMode.HTML
        )

    elif btn_text == "📖 How to Use":
        await msg.reply_text(
            "📖 <b>Quick User Guide:</b>\n"
            f"{get_divider()}\n"
            "1️⃣ <b>Deposit Funds:</b> Use UPI (Auto) or Crypto.\n"
            "2️⃣ <b>Select Product:</b> Choose Country & Quantity.\n"
            "3️⃣ <b>Get OTP:</b> Go to 'My Profile' > 'Orders' > 'Get OTP'.\n"
            "4️⃣ <b>Safety:</b> Always use fresh IPs/Proxy.",
            parse_mode=enums.ParseMode.HTML
        )

# ==================================================================
# 👤 PROFILE DASHBOARD
# ==================================================================

@Client.on_callback_query(filters.regex("my_profile"))
async def profile_callback(c, cb):
    await cb.answer()
    await show_profile_ui(c, cb)

async def show_profile_ui(c, source):
    user_id = source.from_user.id
    user = await get_user(user_id)
    if not user:
        await add_user(user_id, source.from_user.first_name)
        user = await get_user(user_id)

    balance = user.get("balance", 0.0) if user else 0.0
    if isinstance(balance, str): balance = 0.0
    
    total_dep = user.get("total_deposit", 0.0) if user else 0.0
    
    text = (
        "<b>👤 ACCOUNT DASHBOARD</b>\n"
        f"{get_divider()}\n\n"
        f"🆔 <b>User ID:</b> <code>{user_id}</code>\n"
        f"👤 <b>Name:</b> {source.from_user.first_name}\n\n"
        
        "<b>👛 WALLET DETAILS</b>\n"
        f"├ <b>Balance:</b> {format_price(balance)}\n"
        f"└ <b>Total Deposit:</b> {format_price(total_dep)}\n\n"
        
        "<b>📊 ACCOUNT STATUS</b>\n"
        f"├ <b>Terms:</b> ✅ Accepted\n"
        f"└ <b>FSub:</b> ✅ Verified\n"
        f"{get_divider()}"
    )
    
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Deposit Now", callback_data="deposit_home")],
        [
            InlineKeyboardButton("🛍 My Orders", callback_data="my_orders_list"),
            InlineKeyboardButton("💸 My Payments", callback_data="my_payments_list")
        ],
        [InlineKeyboardButton("🔙 Back to Menu", callback_data="home")]
    ])
    
    await safe_send(source, text, buttons)

# ==================================================================
# 🔄 CALLBACK HANDLERS
# ==================================================================

@Client.on_callback_query(filters.regex("accept_terms"))
async def accept_terms_callback(c, cb):
    await toggle_terms(cb.from_user.id, True)
    await cb.answer("✅ Terms Accepted Successfully!", show_alert=True)
    
    is_joined, link = await check_fsub_status(c, cb.from_user.id)
    if not is_joined:
        await show_fsub(c, cb.message, link)
    else:
        await show_main_menu(c, cb)

# ==================================================================
# 📜 HISTORY HANDLERS (Orders & Payments)
# ==================================================================

@Client.on_callback_query(filters.regex(r"^(my_orders_list|page_orders)"))
async def show_orders_history(c, cb):
    user_id = cb.from_user.id
    
    # Page extraction logic
    page = 1
    if "page_orders" in cb.data:
        page = int(cb.data.split("_")[-1])

    # Fetch Orders 
    cursor = col_orders.find({"user_id": user_id}).sort("date", -1)
    orders = await cursor.to_list(length=None)
    
    if not orders:
        try: await cb.answer("❌ You haven't purchased anything yet!", show_alert=True)
        except: pass
        return

    usdt_rate = await get_live_usdt_rate()
    rate = usdt_rate if usdt_rate and usdt_rate > 0 else 90.0
    # Build List Items
    items_list = []
    for o in orders:
        flag = o.get("flag", "🏳️")
        country = o.get("country", "Unknown")
        price = o.get("price", 0)
        order_type = str(o.get("type", "account")).lower().rstrip("s")
        
        price_usdt = round(price / rate, 2)
        type_icon = "📂" if order_type == "session" else "📱"
        text = f"{flag} {country} ({type_icon}) - ₹{price} (${price_usdt})"

        callback = f"view_session_{str(o['_id'])}"
        
        items_list.append({
            "text": text,
            "callback_data": callback
        })


    # Pagination
    kb = get_pagination_keyboard(
        current_page=page,
        total_count=len(items_list),
        data_list=items_list,
        callback_prefix="page_orders",
        row_width=1
    )
    
    # Back Button
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back to Profile", callback_data="my_profile")])

    await safe_send(cb, "<b>🛍 MY ORDERS HISTORY</b>\n<i>Click to view details/OTP.</i>", kb)


@Client.on_callback_query(filters.regex(r"^(my_payments_list|page_payments)"))
async def show_payments_history(c, cb):
    user_id = cb.from_user.id
    
    # Page extraction
    page = 1
    if "page_payments" in cb.data:
        page = int(cb.data.split("_")[-1])

    # Fetch Payments
    cursor = col_payments.find({"user_id": user_id}).sort("date", -1)
    payments = await cursor.to_list(length=None)
    
    if not payments:
        try: await cb.answer("❌ No payment history found!", show_alert=True)
        except: pass
        return

    items_list = []
    for p in payments:
        # Format: ₹200 (UPI)
        status_map = {
            "success": "✅",
            "pending": "⏳",
            "rejected": "❌",
            "refunded": "🔄"
        }
        icon = status_map.get(p.get("status"), "❓")
        amount = p.get("amount", 0)
        method = p.get("method", "Unknown").upper()
        
        text = f"{icon} ₹{amount} ({method})"

        items_list.append({
            "text": text,
            "callback_data": "ignore" 
        })

    # Pagination
    kb = get_pagination_keyboard(
        current_page=page,
        total_count=len(items_list),
        data_list=items_list,
        callback_prefix="page_payments",
        row_width=1
    )

    # Back Button
    kb.inline_keyboard.append([InlineKeyboardButton("🔙 Back to Profile", callback_data="my_profile")])

    await safe_send(cb, "<b>💸 MY PAYMENT HISTORY</b>\n<i>Last 50 Transactions.</i>", kb)

@Client.on_callback_query(filters.regex("^ignore_history$"))
async def ignore_history_click(c, cb):
    await cb.answer()

                                                          
@Client.on_callback_query(filters.regex("verify_fsub"))
async def verify_fsub_callback(c, cb):
    is_joined, link = await check_fsub_status(c, cb.from_user.id)
    if is_joined:
        await cb.answer("✅ Verified!", show_alert=False)
        await show_main_menu(c, cb)
    else:
        await cb.answer("❌ You haven't joined yet!", show_alert=True)

@Client.on_callback_query(filters.regex("^home$")) 
async def back_to_home(c, cb):
    await cb.answer()
    await show_main_menu(c, cb)

@Client.on_callback_query(filters.regex("decline_terms"))
async def decline_handler(c, cb):
    await cb.answer("❌ You must accept the terms to use the bot.", show_alert=True)
