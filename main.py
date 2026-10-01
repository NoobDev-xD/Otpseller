import asyncio
import logging
import sys
from utils.session_patch import patch_hydrogram_storage

# Globally enable multi-format session string support before any Client initializes
patch_hydrogram_storage()

from config import API_ID, API_HASH, BOT_TOKEN, ADMINS, LOG_CHANNEL, ADMIN_GROUP_ID, SESSION_STRING
from hydrogram import Client, idle, enums
from database import ping_db

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = None


# ================= START BOT =================
async def start_bot():
    global app

    # Validate credentials before starting
    if not BOT_TOKEN or not API_ID or not API_HASH:
        logger.error("❌ FATAL ERROR: Missing required credentials (BOT_TOKEN, API_ID, or API_HASH)")
        logger.error("Please check your .env file and ensure all required values are set.")
        sys.exit(1)

    try:
        import os
        workers_count = min(32, (os.cpu_count() or 4) * 8)
        app = Client(
            "SimpleStoreUltimate",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            session_string=SESSION_STRING,
            in_memory=True,
            plugins=dict(root="plugins"),
            parse_mode=enums.ParseMode.HTML,
            no_updates=False,
            workers=workers_count,
            sleep_threshold=10,
            max_concurrent_transmissions=10
        )
    except Exception as e:
        logger.error(f"❌ Failed to initialize Telegram Client: {e}")
        sys.exit(1)

    # Verify Database Connection & Build Indexes
    logger.info("🔄 Checking database connection...")
    db_connected = await ping_db()
    if not db_connected:
        logger.warning("⚠️ Warning: MongoDB not connected. Some features may not work.")
    else:
        logger.info("✅ MongoDB Connected!")
        try:
            from database import create_db_indexes
            await create_db_indexes()
        except Exception as e:
            logger.warning(f"Index check notice: {e}")

    try:
        await app.start()
        logger.info("✅ Telegram bot started successfully")
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        logger.error("Check if BOT_TOKEN is valid and not expired.")
        sys.exit(1)

    try:
        me = await app.get_me()
        logger.info(f"✅ Bot authenticated as @{me.username} (ID: {me.id})")
    except Exception as e:
        logger.error(f"❌ Failed to get bot info: {e}")
        await app.stop()
        sys.exit(1)

    admin_text = (
        "<b>⚡ SYSTEM REBOOTED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 <b>Welcome Back, Master!</b>\n\n"
        f"🤖 <b>Identity:</b> @{me.username}\n"
        "⚙️ <b>Version:</b> <code>v7.5 (Ultimate)</code>\n"
        "🛡️ <b>Security:</b> <code>Active & Encrypted</code>\n"
        "📶 <b>Connection:</b> <code>Stable</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>🚀 All systems nominal. Waiting for commands...</i>"
    )

    log_text = (
        "<blockquote><b>🖥️ SERVER BOOT SEQUENCE</b></blockquote>\n"
        f"🤖 <b>Bot:</b> @{me.username}\n"
        f"🆔 <b>ID:</b> <code>{me.id}</code>\n"
        f"🐍 <b>Python:</b> <code>{sys.version.split()[0]}</code>\n"
        "📂 <b>Modules:</b> <code>Loaded Successfully</code>\n"
        f"🗄️ <b>Database:</b> <code>{'Connected ✅' if db_connected else 'Disconnected ⚠️'}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "✅ <b>STATUS:</b> 🟢 <b>OPERATIONAL</b>"
    )

    # Send notifications
    notification_count = 0
    
    if ADMINS:
        try:
            await app.send_message(ADMINS[0], admin_text)
            logger.info(f"✅ Boot notification sent to admin {ADMINS[0]}")
            notification_count += 1
        except Exception as e:
            logger.warning(f"⚠️ Could not send message to admin {ADMINS[0]}: {e}")

    if ADMIN_GROUP_ID and ADMIN_GROUP_ID != 0:
        try:
            await app.send_message(ADMIN_GROUP_ID, log_text)
            logger.info(f"✅ Boot log sent to group {ADMIN_GROUP_ID}")
            notification_count += 1
        except Exception as e:
            logger.warning(f"⚠️ Could not send message to log group {ADMIN_GROUP_ID}: {e}")

    if notification_count == 0:
        logger.info("ℹ️ No admin notifications sent. Make sure admin IDs are configured in .env")

    logger.info("🚀 Bot is now running and waiting for messages...")
    await idle()
    await app.stop()
    logger.info("✅ Bot stopped gracefully")


# ================= MAIN =================
if __name__ == "__main__":
    try:
        asyncio.run(start_bot())
    except KeyboardInterrupt:
        logger.info("⚠️ Bot stopped by user (Ctrl+C)")
        print("Bot stopped")
    except Exception as e:
        logger.error(f"🔥 Fatal Error: {e}")
        print(f"🔥 Fatal Error: {e}")
        sys.exit(1)