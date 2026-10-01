import asyncio
from hydrogram import Client, filters, enums
from hydrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import ADMINS, ADMIN_GROUP_ID
from database import create_coupon, redeem_coupon_db, update_balance, get_user
from utils import get_divider, format_price

@Client.on_message(filters.command("add_redeem") & filters.user(ADMINS))
async def create_redeem_handler(c, msg):
    try:
        parts = msg.text.split()
        if len(parts) != 4:
            return await msg.reply_text(
                "<b>⚠️ Incorrect Format!</b>\n\n"
                "Use: <code>/add_redeem CODE AMOUNT LIMIT</code>\n"
                "Ex: <code>/add_redeem NEWUSER 20 100</code>"
            )

        code = parts[1].upper()
        amount = int(parts[2])
        limit = int(parts[3])

        await create_coupon(code, amount, limit)

        await msg.reply_text(
            f"<b>✅ COUPON CREATED!</b>\n"
            f"{get_divider()}\n"
            f"🎟️ <b>Code:</b> <code>{code}</code>\n"
            f"💰 <b>Value:</b> ₹{amount}\n"
            f"👥 <b>Limit:</b> {limit} Users\n"
            f"{get_divider()}",
            parse_mode=enums.ParseMode.HTML
        )

        if ADMIN_GROUP_ID and ADMIN_GROUP_ID != 0:
            try:
                await c.send_message(
                    ADMIN_GROUP_ID,
                    f"<b>🎟️ NEW COUPON GENERATED</b>\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                    f"🧑‍💻 <b>By Admin:</b> {msg.from_user.mention}\n"
                    f"🎟️ <b>Code:</b> <code>{code}</code>\n"
                    f"💰 <b>Value:</b> ₹{amount}\n"
                    f"👥 <b>Limit:</b> {limit} Users",
                    parse_mode=enums.ParseMode.HTML
                )
            except Exception as log_e:
                print(f"Coupon Log Error: {log_e}")

    except ValueError:
        await msg.reply_text("❌ Error: Amount and Limit must be numbers.")
    except Exception as e:
        await msg.reply_text(f"❌ Error: {e}")

@Client.on_message(filters.command("redeem"))
async def redeem_handler(c, msg):
    try:
        parts = msg.text.split()
        if len(parts) != 2:
            return await msg.reply_text("❌ <b>Usage:</b> `/redeem YOUR_CODE`")

        code = parts[1].upper()
        user_id = msg.from_user.id
        user_name = msg.from_user.first_name

        success, message, amount = await redeem_coupon_db(user_id, code)

        if success:
            await update_balance(user_id, amount)
            
            await msg.reply_text(
                f"<b>🎉 CONGRATULATIONS!</b>\n"
                f"{get_divider()}\n"
                f"✅ Coupon Redeemed Successfully.\n"
                f"💰 <b>Added:</b> ₹{amount}\n"
                f"👛 <b>Check Balance:</b> /start\n"
                f"{get_divider()}",
                parse_mode=enums.ParseMode.HTML
            )

            if ADMIN_GROUP_ID and ADMIN_GROUP_ID != 0:
                try:
                    await c.send_message(
                        ADMIN_GROUP_ID,
                        f"<b>🎁 COUPON REDEEMED</b>\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"👤 <b>User:</b> {msg.from_user.mention} (<code>{user_id}</code>)\n"
                        f"🎟️ <b>Code:</b> <code>{code}</code>\n"
                        f"💰 <b>Added:</b> ₹{amount}",
                        parse_mode=enums.ParseMode.HTML
                    )
                except Exception as log_e:
                    print(f"Coupon Log Error: {log_e}")

        else:
            await msg.reply_text(f"⚠️ {message}")

    except Exception as e:
        await msg.reply_text(f"❌ Error: {e}")
