import datetime
import uuid
from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI
import logging

logger = logging.getLogger(__name__)

# ==================================================================
# 🔌 MONGODB CONNECTION
# ==================================================================
client = None
db = None
col_users = None
col_stock = None
col_orders = None
col_payments = None
col_fsub = None
col_settings = None
col_coupons = None
col_sessions = None

def init_db():
    """Initialize MongoDB connection"""
    global client, db, col_users, col_stock, col_orders, col_payments, col_fsub, col_settings, col_coupons, col_sessions
    
    if not MONGO_URI:
        logger.error("❌ MONGO_URI is not configured in .env file")
        return False
    
    try:
        try:
            import certifi
            ca_file = certifi.where()
        except ImportError:
            ca_file = None

        kwargs = {"serverSelectionTimeoutMS": 5000}
        if ca_file:
            kwargs["tlsCAFile"] = ca_file
        else:
            kwargs["tlsAllowInvalidCertificates"] = True

        client = AsyncIOMotorClient(MONGO_URI, **kwargs)
        db = client["store_bot"]
        col_users = db["users"]
        col_stock = db["stock"]
        col_orders = db["orders"]
        col_payments = db["payments"]
        col_fsub = db["fsub"]
        col_settings = db["settings"]
        col_coupons = db["coupons"]
        col_sessions = db["sessions"]
        logger.info("✅ MongoDB Client initialized successfully")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to initialize MongoDB Client: {e}")
        client = None
        db = None
        col_users = None
        col_stock = None
        col_orders = None
        col_payments = None
        col_fsub = None
        col_settings = None
        col_coupons = None
        col_sessions = None
        return False

async def ping_db():
    """Tests the connection to MongoDB."""
    if client is None:
        logger.warning("⚠️ MongoDB client not initialized")
        return False
    try:
        await client.admin.command('ping')
        logger.info("✅ MongoDB ping successful")
        return True
    except Exception as e:
        logger.error(f"❌ MongoDB connection failed: {e}")
        return False

async def create_db_indexes():
    """Creates performance indexes for ultra-fast queries and aggregations."""
    try:
        check_db()
        # Stock indexes for instant bucket aggregations and duplicate checks
        await col_stock.create_index([("status", 1), ("country", 1), ("type", 1), ("price", 1), ("year", 1)])
        await col_stock.create_index([("data", 1)])
        await col_stock.create_index([("phone", 1)])
        await col_stock.create_index([("status", 1), ("category", 1)])
        
        # User indexes for instant balance lookups
        await col_users.create_index([("_id", 1)])
        
        # Order indexes for instant order retrieval
        await col_orders.create_index([("user_id", 1), ("date", -1)])
        await col_orders.create_index([("_id", 1)])
        
        # Payments indexes
        await col_payments.create_index([("user_id", 1)])
        await col_payments.create_index([("txn_id", 1)])
        logger.info("⚡ MongoDB performance indexes verified/created successfully.")
    except Exception as e:
        logger.warning(f"Index creation notice: {e}")

# Initialize DB on import
init_db()

# Helper function to check DB before operations
def check_db():
    """Raises error if DB is not connected"""
    if db is None:
        raise RuntimeError("❌ Database not initialized. Check MONGO_URI in .env file.")

# ==================================================================
# 🔌 SESSION MANAGEMENT (For Persistence across restarts)
# ==================================================================

async def set_session(user_id, session_data):
    check_db()
    await col_sessions.update_one(
        {"_id": user_id},
        {"$set": session_data},
        upsert=True
    )

async def get_session(user_id):
    check_db()
    return await col_sessions.find_one({"_id": user_id})

async def del_session(user_id):
    check_db()
    await col_sessions.delete_one({"_id": user_id})


# ==================================================================
# 👤 1. USER MANAGEMENT
# ==================================================================

async def add_user(user_id, name):
    check_db()
    user = await col_users.find_one({"_id": user_id})
    if not user:
        await col_users.insert_one({
            "_id": user_id,
            "name": name,
            "balance": 0.0,
            "total_deposit": 0.0,
            "terms_accepted": False,
            "join_date": datetime.datetime.now()
        })

async def toggle_terms(user_id, status: bool):
    """Saves terms acceptance status."""
    check_db()
    await col_users.update_one({"_id": user_id}, {"$set": {"terms_accepted": status}}, upsert=True)
    return True

async def get_user(user_id):
    check_db()
    return await col_users.find_one({"_id": user_id})

async def update_balance(user_id, amount):
    """Updates user balance and total deposit."""
    check_db()
    update_data = {"$inc": {"balance": float(amount)}}
    if amount > 0:
        update_data["$inc"]["total_deposit"] = float(amount)
    
    result = await col_users.update_one({"_id": user_id}, update_data)
    return result.modified_count > 0

# ==================================================================
# 📦 2. STOCK MANAGEMENT
# ==================================================================

async def add_stock(category, items_list):
    check_db()
    if not items_list: return 0
    for item in items_list:
        item["_id"] = str(uuid.uuid4())
        item["category"] = category
        item["date_added"] = datetime.datetime.now()
        item["status"] = item.get("status", "fresh")
    
    result = await col_stock.insert_many(items_list)
    return len(result.inserted_ids)

async def get_unique_buckets(target_type=None):
    """Groups fresh stock by Country + Price + Year using Aggregation."""
    check_db()
    pipeline = [
        {"$match": {"status": "fresh"}},
        {"$group": {
            "_id": {
                "country": "$country",
                "price": "$price",
                "year": "$year",
                "flag": "$flag"
            },
            "count": {"$sum": 1},
            "sample_id": {"$first": "$_id"},
            "type": {"$first": "$type"}
        }},
        {"$sort": {"_id.country": 1}}
    ]
    return await col_stock.aggregate(pipeline).to_list(None)

async def update_bucket_price(country, year, old_price, new_price):
    """Updates the price of all matching fresh items."""
    check_db()
    res = await col_stock.update_many(
        {"country": country, "year": year, "price": int(old_price), "status": "fresh"},
        {"$set": {"price": int(new_price)}}
    )
    return res.modified_count

async def delete_stock_item(item_id):
    """Deletes a single item from stock."""
    check_db()
    res = await col_stock.delete_one({"_id": item_id})
    return res.deleted_count > 0

async def get_stock_stats(target_type=None):
    check_db()
    buckets = await get_unique_buckets(target_type)
    stats = []
    for b in buckets:
        stats.append({
            "_id": str(b["sample_id"]), 
            "country": b["_id"]["country"],
            "price": b["_id"]["price"],
            "year": b["_id"]["year"],
            "flag": b["_id"].get("flag", "🏳️"),
            "count": b["count"],
            "type": b.get("type", "session")
        })
    return stats

async def get_unique_countries():
    check_db()
    pipeline = [
        {"$match": {"status": "fresh"}},
        {"$group": {
            "_id": "$country",
            "flag": {"$first": "$flag"}
        }},
        {"$sort": {"_id": 1}}
    ]
    return await col_stock.aggregate(pipeline).to_list(None)

async def get_country_stats(item_type=None):
    check_db()
    pipeline = [
        {"$match": {"status": "fresh"}},
        {"$group": {
            "_id": "$country",
            "flag": {"$first": "$flag"},
            "count": {"$sum": 1},
            "min_price": {"$min": "$price"}
        }},
        {"$sort": {"_id": 1}}
    ]
    return await col_stock.aggregate(pipeline).to_list(None)

async def get_buckets_by_country(country_name, item_type=None):
    check_db()
    match_query = {"status": "fresh", "country": country_name}
        
    pipeline = [
        {"$match": match_query},
        {"$group": {
            "_id": {
                "price": "$price",
                "year": "$year"
            },
            "sample_id": {"$first": "$_id"},
            "flag": {"$first": "$flag"},
            "count": {"$sum": 1},
            "type": {"$first": "$type"}
        }},
        {"$sort": {"_id.price": 1}}
    ]
    results = await col_stock.aggregate(pipeline).to_list(None)
    stats = []
    for r in results:
        stats.append({
            "_id": str(r["sample_id"]),
            "country": country_name,
            "price": r["_id"]["price"],
            "year": r["_id"]["year"],
            "flag": r["flag"],
            "count": r["count"],
            "type": r.get("type", "account")
        })
    return stats

async def get_product_details(product_id):
    check_db()
    return await col_stock.find_one({"_id": str(product_id)})

async def get_stock_count(country, item_type, price, year):
    check_db()
    try:
        p_int = int(price)
        p_flt = float(price)
        price_match = {"$in": [p_int, p_flt, str(p_int), str(price)]}
    except:
        price_match = price
    year_str = str(year)
    year_match = {"$in": [year_str, int(year_str)]} if year_str.isdigit() else year_str

    return await col_stock.count_documents({
        "status": "fresh",
        "country": country,
        "price": price_match,
        "year": year_match
    })

# ==================================================================
# 🛒 3. BUYING LOGIC
# ==================================================================

async def buy_bucket_atomic(user_id, country, year, price, item_type=None):
    """Finds ANY fresh item in the bucket and buys it atomically."""
    check_db()
    try:
        p_int = int(price)
        p_flt = float(price)
        price_match = {"$in": [p_int, p_flt, str(p_int), str(price)]}
    except:
        price_match = price
    year_str = str(year)
    year_match = {"$in": [year_str, int(year_str)]} if year_str.isdigit() else year_str

    # 1. Find and update ANY fresh item in this bucket
    item = await col_stock.find_one_and_update(
        {
            "country": country, 
            "year": year_match, 
            "price": price_match, 
            "status": "fresh"
        },
        {"$set": {"status": "sold", "sold_to": user_id, "sold_at": datetime.datetime.now()}},
        return_document=True
    )
    
    if not item:
        return None

    # 2. Check balance and deduct
    price_val = float(item.get("price", 0))
    user = await col_users.find_one_and_update(
        {"_id": user_id, "balance": {"$gte": price_val}},
        {"$inc": {"balance": -price_val}},
        return_document=True
    )

    if not user:
        # Rollback stock if balance check fails
        await col_stock.update_one({"_id": item["_id"]}, {"$set": {"status": "fresh"}, "$unset": {"sold_to": "", "sold_at": ""}})
        return None

    # 3. Create Order Record
    order_id = str(uuid.uuid4())
    order_data = {
        "_id": order_id,
        "user_id": user_id,
        "item_id": item["_id"],
        "data": item.get("data"),
        "phone": item.get("phone"),
        "price": price_val,
        "country": item.get("country", "Unknown"),
        "flag": item.get("flag", "🏳️"),
        "type": item.get("type", item_type or "account"),
        "date": datetime.datetime.now(),
        "otp": None
    }
    await col_orders.insert_one(order_data)
    return order_data

async def buy_item_atomic(user_id, product_id, category):
    """MongoDB Atomic Purchase logic."""
    check_db()
    # 1. Find and update stock item to 'sold' status atomically
    item = await col_stock.find_one_and_update(
        {"_id": str(product_id), "status": "fresh"},
        {"$set": {"status": "sold", "sold_to": user_id, "sold_at": datetime.datetime.now()}},
        return_document=True
    )
    
    if not item:
        return None

    # 2. Check balance and deduct
    price = float(item.get("price", 0))
    user = await col_users.find_one_and_update(
        {"_id": user_id, "balance": {"$gte": price}},
        {"$inc": {"balance": -price}},
        return_document=True
    )

    if not user:
        # Rollback stock if balance check fails
        await col_stock.update_one({"_id": str(product_id)}, {"$set": {"status": "fresh"}, "$unset": {"sold_to": "", "sold_at": ""}})
        return None

    # 3. Create Order Record
    order_id = str(uuid.uuid4())
    order_data = {
        "_id": order_id,
        "user_id": user_id,
        "item_id": str(product_id),
        "data": item.get("data"),
        "phone": item.get("phone"),
        "price": price,
        "country": item.get("country", "Unknown"),
        "flag": item.get("flag", "🏳️"),
        "type": "session" if category == "sessions" else "account",
        "date": datetime.datetime.now(),
        "otp": None
    }
    await col_orders.insert_one(order_data)
    return order_data

async def get_order(order_id):
    check_db()
    return await col_orders.find_one({"_id": str(order_id)})

# ==================================================================
# 💰 4. PAYMENTS & DEPOSITS
# ==================================================================

async def get_deposit(utr):
    check_db()
    return await col_payments.find_one({"utr": str(utr)})

async def create_deposit(user_id, amount, utr, method, status="pending"):
    check_db()
    if await get_deposit(utr):
        return "duplicate"

    payment_data = {
        "_id": str(uuid.uuid4()),
        "user_id": user_id,
        "amount": amount,
        "utr": utr,
        "method": method,
        "status": status,
        "date": datetime.datetime.now()
    }
    await col_payments.insert_one(payment_data)
    return "created"

# ==================================================================
# 📢 FSUB SETTINGS
# ==================================================================

async def add_fsub(chat_id, invite_link, title):
    check_db()
    await col_fsub.update_one(
        {"_id": chat_id},
        {"$set": {"link": invite_link, "title": title}},
        upsert=True
    )
    return True

async def get_fsub_list():
    check_db()
    return await col_fsub.find().to_list(None)

async def del_fsub(chat_id):
    check_db()
    result = await col_fsub.delete_one({"_id": chat_id})
    return result.deleted_count > 0

async def update_fsub(chat_id, invite_link=None, title="Channel"):
    check_db()
    update_data = {"title": title}
    if invite_link:
        update_data["link"] = invite_link
    await col_fsub.update_one({"_id": chat_id}, {"$set": update_data}, upsert=True)
    return True

# ==================================================================
# 🎟️ REDEEM / COUPON SYSTEM
# ==================================================================

async def create_coupon(code: str, amount: int, limit: int):
    check_db()
    await col_coupons.update_one(
        {"code": code},
        {"$set": {
            "code": code,
            "amount": amount,
            "limit": int(limit),
            "used_count": 0,
            "used_by": []
        }},
        upsert=True
    )
    return True

async def get_coupon(code: str):
    check_db()
    return await col_coupons.find_one({"code": code})

async def redeem_coupon_db(user_id, code):
    check_db()
    coupon = await col_coupons.find_one({"code": code})
    if not coupon:
        return False, "❌ Invalid Code!", 0
        
    if int(coupon["used_count"]) >= int(coupon["limit"]):
        return False, "❌ Coupon Limit Reached!", 0
        
    if user_id in coupon.get("used_by", []):
        return False, "⚠️ You have already used this coupon!", 0
        
    result = await col_coupons.update_one(
        {"code": code, "used_count": {"$lt": coupon["limit"]}, "used_by": {"$ne": user_id}},
        {"$inc": {"used_count": 1}, "$push": {"used_by": user_id}}
    )
    
    if result.modified_count > 0:
        return True, "✅ Coupon Redeemed!", int(coupon["amount"])
    return False, "❌ Redemption Failed!", 0

# ==================================================================
# 🤝 REFERRAL SYSTEM
# ==================================================================

async def set_referrer(new_user_id, referrer_id):
    check_db()
    if str(new_user_id) == str(referrer_id):
        return False 
    
    result = await col_users.update_one(
        {"_id": new_user_id, "referred_by": {"$exists": False}},
        {"$set": {"referred_by": str(referrer_id), "referral_paid": False}}
    )
    return result.modified_count > 0

async def check_referral_milestone(user_id, current_deposit_amount):
    check_db()
    user = await col_users.find_one({"_id": user_id})
    if not user or not user.get("referred_by") or user.get("referral_paid"):
        return None
        
    if user.get("total_deposit", 0) >= 1000:
        referrer_id = user["referred_by"]
        await update_balance(referrer_id, 20)
        await col_users.update_one({"_id": user_id}, {"$set": {"referral_paid": True}})
        return referrer_id
    return None

# ==================================================================
# 🚧 MAINTENANCE & GLOBAL SETTINGS
# ==================================================================

async def set_maintenance(status: bool):
    check_db()
    await col_settings.update_one(
        {"_id": "main_config"},
        {"$set": {"maintenance": status}},
        upsert=True
    )
    return True

async def get_maintenance():
    check_db()
    config = await col_settings.find_one({"_id": "main_config"})
    return config.get("maintenance", False) if config else False

async def update_usdt_rate(rate: float):
    check_db()
    await col_settings.update_one(
        {"_id": "main_config"},
        {"$set": {"usdt_rate": float(rate)}},
        upsert=True
    )
    return True

async def get_live_usdt_rate():
    """Gets the live USDT rate from settings, defaulting to a safe positive rate."""
    check_db()
    config_doc = await col_settings.find_one({"_id": "main_config"})
    if config_doc and "usdt_rate" in config_doc:
        try:
            rate = float(config_doc["usdt_rate"])
            if rate > 0:
                return rate
        except (ValueError, TypeError):
            pass
    from config import USDT_RATE
    try:
        rate = float(USDT_RATE)
        if rate > 0:
            return rate
    except (ValueError, TypeError):
        pass
    return 100