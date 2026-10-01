import asyncio
import aiohttp
import qrcode
import io
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ForceReply, CallbackQuery, Message
from config import ADMINS, PAYMENT_UPI_ID, BINANCE_ID, BEP20_ADDRESS, ADMIN_GROUP_ID
from database import get_user, update_balance, create_deposit, get_deposit, get_session, set_session, del_session
from utils import format_price
# In buttons ko yahan define kar dein taaki NameError na aaye
MAIN_BUTTONS = [
    "📱 Buy Accounts", "📂 Buy Sessions", 
    "👛 Add Funds", "👤 My Profile", 
    "💰 Earn Money", "📞 Support", "📖 How to Use"
]

# ==================================================================
# 🏦 DEPOSIT MENU 
# ==================================================================
async def safe_deposit_menu(client, message_or_callback):
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇳 UPI (PhonePe/GPay/Paytm)", callback_data="pay_upi_start")],
        [InlineKeyboardButton("🪙 Crypto (Manual)", callback_data="pay_crypto")],
        [InlineKeyboardButton("🔙 Back to Home", callback_data="home")]
    ])
    user_id = message_or_callback.from_user.id
    await del_session(user_id)

    try:
        user = await get_user(user_id)
        balance_val = float(user.get("balance", 0)) if user else 0.0
        text = (
            f"<b>🏦 ADD FUNDS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Wallet Balance:</b> {format_price(balance_val)}\n\n"
            "👇 <b>Select Payment Method:</b>"
        )

        if isinstance(message_or_callback, CallbackQuery):
            await message_or_callback.message.edit_text(text, reply_markup=buttons)
        else:
            await message_or_callback.reply_text(text, reply_markup=buttons)
    except Exception as e:
        await client.send_message(user_id, "<b>🏦 ADD FUNDS</b>\nSelect Method:", reply_markup=buttons)

@Client.on_message(filters.command("deposit"))
async def deposit_command(c, msg):
    if len(msg.command) > 1:
        amount_str = msg.command[1]
        if amount_str.isdigit():
            amount = int(amount_str)
            return await send_upi_qr(c, msg.from_user.id, amount)
    await safe_deposit_menu(c, msg)

@Client.on_callback_query(filters.regex("deposit_home"))
async def deposit_callback(c, cb): await safe_deposit_menu(c, cb)

# =======================================
# ==================================================================
# 🇮🇳 UPI AUTOMATIC FLOW (Step 1: Ask Amount)
# ==================================================================
@Client.on_callback_query(filters.regex("pay_upi_start"))
async def pay_upi_ask_amount(c, cb):
    user_id = cb.from_user.id
    await set_session(user_id, {"mode": "waiting_amount"})
    
    await cb.message.edit_text(
        "💰 <b>ENTER AMOUNT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Send the Amount You want deposit?\n"
        "<i>Example: 50, 100, 500</i>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="deposit_home")]])
    )

# ==================================================================
# 📸 UPI QR SENDER (Shared Logic)
# ==================================================================
async def send_upi_qr(c, user_id, amount):
    await set_session(user_id, {"mode": "waiting_payment", "amount": amount})
    
    # Generate Dynamic UPI QR
    upi_url = f"upi://pay?pa={PAYMENT_UPI_ID}&pn=APT_SMM&am={amount}&cu=INR"
    qr = qrcode.make(upi_url)
    buf = io.BytesIO()
    qr.save(buf)
    buf.seek(0)

    text = (
        f"<b>💳 UPI PAYMENT - ₹{amount}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>UPI ID:</b> <code>{PAYMENT_UPI_ID}</code>\n\n"
        "1️⃣ Scan the QR code using any UPI app (PhonePe, GPay, Paytm, etc.)\n"
        "2️⃣ After successful payment, click '✅ I HAVE PAID' and send the screenshot."
    )
    
    await c.send_photo(
        user_id, 
        photo=buf, 
        caption=text, 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ I HAVE PAID", callback_data="i_have_paid")]])
    )

# ==================================================================
# 📸 STEP 2: Show QR & I Have Paid Button
# ==================================================================
@Client.on_message(filters.text & filters.private, group=3)
async def handle_deposit_amount(c, msg):
    user_id = msg.from_user.id
    session = await get_session(user_id)
    if not session or session.get("mode") != "waiting_amount":
        return

    if not msg.text.isdigit():
        return await msg.reply_text("❌ Please enter a valid number (Amount).")

    amount = int(msg.text)
    await send_upi_qr(c, user_id, amount)

# 📸 STEP 3: Ask Screenshot
# ==================================================================
@Client.on_callback_query(filters.regex("i_have_paid"))
async def ask_screenshot_after_pay(c, cb):
    user_id = cb.from_user.id
    session = await get_session(user_id)
    if not session or "amount" not in session:
        return await cb.answer("❌ Session expired. Start again.", show_alert=True)

    await set_session(user_id, {"mode": "waiting_screenshot", "amount": session["amount"]})
    await cb.message.delete()
    await c.send_message(user_id, "📸 <b>Now send payment screenshot:</b>", reply_markup=ForceReply(selective=True))



@Client.on_message(filters.photo & filters.private, group=4)
async def handle_upi_screenshot(c, msg):
    user_id = msg.from_user.id

    session = await get_session(user_id)
    if not session or session.get("mode") != "waiting_screenshot":
        return

    amount = session["amount"]

    if ADMIN_GROUP_ID and ADMIN_GROUP_ID != 0:
        await c.send_photo(
            ADMIN_GROUP_ID,
            photo=msg.photo.file_id,
            caption=(
                f"💰 <b>UPI Deposit Request</b>\n"
                f"User: `{user_id}`\n"
                f"Amount: ₹{amount}\n"
            ),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{user_id}_{amount}")]
            ])
        )
    elif ADMINS:
        try:
            await c.send_photo(
                ADMINS[0],
                photo=msg.photo.file_id,
                caption=(
                    f"💰 <b>UPI Deposit Request</b>\n"
                    f"User: `{user_id}`\n"
                    f"Amount: ₹{amount}\n"
                ),
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{user_id}_{amount}")]
                ])
            )
        except Exception:
            pass

    await msg.reply_text("✅ <b>Submitted!</b> Wait for admin approval.")

    await del_session(user_id)            
    


# ==================================================================
# 🪙 CRYPTO & ADMIN LOGIC
# ==================================================================
@Client.on_callback_query(filters.regex("pay_crypto"))
async def pay_crypto(c, cb):
    text = f"<b>🪙 CRYPTO DEPOSIT (USDT)</b>\n\n<b>🆔 Binance Pay ID:</b>\n<code>{BINANCE_ID}</code>\n\n<b>🔗 BEP20:</b>\n<code>{BEP20_ADDRESS}</code>"
    await cb.message.edit_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📤 Upload Screenshot", callback_data="submit_crypto_proof")],[InlineKeyboardButton("🔙 Back", callback_data="deposit_home")]]))

@Client.on_callback_query(filters.regex("submit_crypto_proof"))
async def ask_proof(c, cb):
    user_id = cb.from_user.id
    await set_session(user_id, {"mode": "waiting_proof"})
    await cb.message.delete()
    await c.send_message(user_id, "<b>📸 Send Payment Screenshot Now.</b>", reply_markup=ForceReply(selective=True))

@Client.on_message(filters.photo & filters.private, group=2)
async def handle_crypto_proof(c, msg):
    user_id = msg.from_user.id
    session = await get_session(user_id)
    if not session or session.get("mode") != "waiting_proof": return
    
    if ADMIN_GROUP_ID and ADMIN_GROUP_ID != 0:
        await c.send_photo(ADMIN_GROUP_ID, photo=msg.photo.file_id, caption=f"🪙 <b>Crypto Proof</b>\nUser: `{user_id}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{user_id}_crypto")]]))
    elif ADMINS:
        try:
            await c.send_photo(ADMINS[0], photo=msg.photo.file_id, caption=f"🪙 <b>Crypto Proof</b>\nUser: `{user_id}`", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{user_id}_crypto")]]))
        except Exception:
            pass
    await msg.reply_text("✅ <b>Submitted!</b> Wait for admin check.")
    await del_session(user_id)

@Client.on_callback_query(filters.regex(r"admin_approve_(\d+)_(.+)"))
async def admin_approve_ask(c, cb):
    parts = cb.data.split("_")
    u_id = int(parts[2])
    extra = parts[3]

    if extra.isdigit():
        amount = int(extra)
        try:
            await update_balance(u_id, amount)
            await cb.message.edit_caption(
                f"{cb.message.caption}\n\n✅ <b>Approved Automatically!</b> Added ₹{amount}"
            )
            await cb.answer("✅ Balance Added!", show_alert=True)
            await c.send_message(u_id, f"✅ <b>Approved!</b> Added ₹{amount}")
        except Exception as e:
            await cb.answer(f"Error: {e}", show_alert=True)
    else:
        await cb.message.reply_text(f"💰 <b>Reply with Amount</b> for User: `{u_id}`", reply_markup=ForceReply(selective=True))

@Client.on_message(filters.reply & filters.regex(r"^\d+$"))
async def admin_finalize(c, msg):
    if msg.reply_to_message and msg.reply_to_message.text and "Reply with Amount" in msg.reply_to_message.text:
        try:
            target_id = int(msg.reply_to_message.text.split("User: `")[1].split("`")[0])
            amount = int(msg.text)
            await update_balance(target_id, amount)
            await msg.reply_text("✅ Done!")
            await c.send_message(target_id, f"✅ <b>Approved!</b> Added ₹{amount}")
        except Exception as e: await msg.reply_text(f"Error: {e}")

@Client.on_callback_query(filters.regex(r"manual_review_(\d+)"))
async def manual_req(c, cb):
    if ADMIN_GROUP_ID and ADMIN_GROUP_ID != 0:
        await c.send_message(ADMIN_GROUP_ID, f"⚠️ <b>Manual Review</b>\nUser: {cb.from_user.id}\nID: {cb.data.split('_')[2]}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Approve", callback_data=f"admin_approve_{cb.from_user.id}_manual")]]))
    await cb.message.edit_text("✅ Sent!")
