import struct
import base64
import logging
import sqlite3
import tempfile
import json
import re
import os
from hydrogram.storage import SQLiteStorage

logger = logging.getLogger(__name__)

def get_default_api_id() -> int:
    try:
        from config import API_ID
        return int(API_ID or 0)
    except Exception:
        return 6  # Telegram default public api_id if none specified


def extract_from_sqlite_bytes(raw_bytes: bytes, default_api_id: int = 0) -> tuple | None:
    """
    Extracts session parameters from a SQLite .session database file (Telethon or Pyrogram format)
    using ultra-fast in-memory deserialization (0 disk I/O).
    """
    if not raw_bytes or not raw_bytes.startswith(b"SQLite format 3\x00"):
        return None

    api_id = default_api_id or get_default_api_id()
    conn = None
    tf_path = None
    try:
        try:
            conn = sqlite3.connect(":memory:")
            conn.deserialize(raw_bytes)
        except Exception:
            with tempfile.NamedTemporaryFile(suffix=".session", delete=False) as tf:
                tf.write(raw_bytes)
                tf_path = tf.name
            conn = sqlite3.connect(tf_path)

        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = {row[0].lower(): row[0] for row in cur.fetchall()}

        if "sessions" in tables:
            tbl_name = tables["sessions"]
            cur.execute(f"PRAGMA table_info({tbl_name})")
            cols = [col[1].lower() for col in cur.fetchall()]
            cur.execute(f"SELECT * FROM {tbl_name} LIMIT 1")
            row = cur.fetchone()

            if row:
                row_dict = dict(zip(cols, row))
                dc_id = row_dict.get("dc_id", 1) or 1
                auth_key = row_dict.get("auth_key")
                test_mode = bool(row_dict.get("test_mode", 0))
                user_id = row_dict.get("user_id", 0) or 0
                is_bot = bool(row_dict.get("is_bot", 0))

                # If user_id is missing, search in entities or peers tables
                if not user_id and "entities" in tables:
                    try:
                        cur.execute(f"SELECT id FROM {tables['entities']} WHERE id > 0 LIMIT 1")
                        erow = cur.fetchone()
                        if erow and erow[0]:
                            user_id = int(erow[0])
                    except Exception:
                        pass

                if not user_id and "peers" in tables:
                    try:
                        cur.execute(f"SELECT id FROM {tables['peers']} WHERE type='user' LIMIT 1")
                        prow = cur.fetchone()
                        if prow and prow[0]:
                            user_id = int(prow[0])
                    except Exception:
                        pass

                if auth_key and isinstance(auth_key, (bytes, bytearray)) and len(auth_key) == 256:
                    return int(dc_id), int(api_id), bool(test_mode), bytes(auth_key), int(user_id), bool(is_bot)

    except Exception as e:
        logger.debug(f"SQLite session extraction failed: {e}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        if tf_path and os.path.exists(tf_path):
            try:
                os.remove(tf_path)
            except Exception:
                pass

    return None


def session_tuple_to_sqlite_bytes(dc_id: int, api_id: int, test_mode: bool, auth_key: bytes, user_id: int = 0, phone: str = "") -> bytes:
    """
    Creates a Universal Telegram SQLite .session database file in memory (0 disk I/O)
    that works seamlessly across Telethon, Pyrogram v1/v2, Hydrogram, and panel/converter tools.
    """
    DC_IPS = {
        1: ("149.154.175.53", 443),
        2: ("149.154.167.51", 443),
        3: ("149.154.175.100", 443),
        4: ("149.154.167.92", 443),
        5: ("91.108.56.130", 443),
    }
    ip, port = DC_IPS.get(dc_id, ("149.154.175.53", 443))
    
    conn = None
    tf_path = None
    try:
        try:
            conn = sqlite3.connect(":memory:")
        except Exception:
            with tempfile.NamedTemporaryFile(suffix=".session", delete=False) as tf:
                tf_path = tf.name
            conn = sqlite3.connect(tf_path)

        cur = conn.cursor()
        
        # 1. Version table (Telethon + Pyrogram)
        cur.execute("CREATE TABLE version (version INTEGER PRIMARY KEY, number INTEGER)")
        cur.execute("INSERT INTO version VALUES (7, 3)")
        
        # 2. Universal sessions table (Telethon + Pyrogram + Hydrogram unified schema)
        cur.execute("""
        CREATE TABLE sessions (
            dc_id INTEGER PRIMARY KEY,
            server_address TEXT,
            port INTEGER,
            auth_key BLOB,
            takeout_id INTEGER,
            test_mode INTEGER DEFAULT 0,
            date INTEGER DEFAULT 0,
            user_id INTEGER DEFAULT 0,
            is_bot INTEGER DEFAULT 0,
            api_id INTEGER DEFAULT 6
        )
        """)
        cur.execute("""
        INSERT INTO sessions (dc_id, server_address, port, auth_key, takeout_id, test_mode, date, user_id, is_bot, api_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (dc_id, ip, port, auth_key, None, 1 if test_mode else 0, 0, user_id or 0, 0, api_id or 6))
        
        # 3. Telethon entities table
        cur.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, hash INTEGER NOT NULL, username TEXT, phone INTEGER, name TEXT, date INTEGER)")
        phone_digits = None
        if phone:
            digits_str = re.sub(r"\D", "", str(phone))
            if digits_str:
                phone_digits = int(digits_str)
        if user_id or phone_digits:
            cur.execute("INSERT INTO entities VALUES (?, 0, NULL, ?, NULL, 0)", (user_id or phone_digits, phone_digits))

        # 4. Pyrogram peers table
        cur.execute("CREATE TABLE peers (id INTEGER PRIMARY KEY, access_hash INTEGER, type TEXT, username TEXT, phone_number TEXT)")
        formatted_phone = f"+{phone_digits}" if phone_digits else (str(phone) if phone else None)
        if user_id or formatted_phone:
            cur.execute("INSERT INTO peers VALUES (?, 0, 'user', NULL, ?)", (user_id or 0, formatted_phone))

        # 5. Telethon additional tables
        cur.execute("CREATE TABLE sent_files (md5_digest BLOB PRIMARY KEY, file_size INTEGER, type INTEGER, id INTEGER, hash INTEGER)")
        cur.execute("CREATE TABLE update_state (id INTEGER PRIMARY KEY, pts INTEGER, qts INTEGER, date INTEGER, seq INTEGER)")
        
        conn.commit()

        try:
            return conn.serialize()
        except Exception:
            conn.close()
            conn = None
            with open(tf_path, "rb") as f:
                return f.read()
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
        if tf_path and os.path.exists(tf_path):
            try:
                os.remove(tf_path)
            except Exception:
                pass


def normalize_session_bytes(raw_bytes: bytes, default_api_id: int = 0) -> tuple:
    """
    Unpacks session bytes from ANY Telegram client format:
    - SQLite binary database (Telethon or Pyrogram .session file, e.g. 28672 bytes)
    - Hydrogram format: >BI?256sQ? (271 bytes)
    - Pyrogram v2 format: >B?256sQ? (267 bytes)
    - Pyrogram v1 format: >B?256sI? (263 bytes)
    - Pyrogram v2 with IP/port padding: (356 bytes)
    - Telethon StringSession IPv4: >B4sH256s (263 bytes)
    - Telethon StringSession IPv6: >B16sH256s (275 bytes)
    - Telethon StringSession Auth-only: >B256s (257 bytes)
    - Raw 256-byte AuthKey

    Returns (dc_id, api_id, test_mode, auth_key, user_id, is_bot).
    """
    if not raw_bytes:
        raise ValueError("Empty session bytes provided")

    length = len(raw_bytes)
    api_id = default_api_id or get_default_api_id()

    # 1. SQLite binary session file (Telethon or Pyrogram .session file)
    if raw_bytes.startswith(b"SQLite format 3\x00"):
        res = extract_from_sqlite_bytes(raw_bytes, api_id)
        if res:
            return res
        raise ValueError(f"SQLite file found ({length} bytes) but contains no valid Telegram session data")

    # 2. Hydrogram format: >BI?256sQ? (271 bytes)
    if length == 271:
        return struct.unpack(">BI?256sQ?", raw_bytes)

    # 3. Pyrogram v2 format: >B?256sQ? (267 bytes)
    elif length == 267:
        dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(">B?256sQ?", raw_bytes)
        return dc_id, api_id, test_mode, auth_key, user_id, is_bot

    # 4. 263 bytes: Pyrogram v1 (>B?256sI?) OR Telethon IPv4 (>B4sH256s)
    elif length == 263:
        # Check if Telethon IPv4 (port is usually 443 or 80 at bytes 5:7)
        port = struct.unpack(">H", raw_bytes[5:7])[0] if len(raw_bytes) >= 7 else 0
        if port in (443, 80, 5222):
            dc_id, ip_bytes, port, auth_key = struct.unpack(">B4sH256s", raw_bytes)
            return dc_id, api_id, False, auth_key, 0, False
        else:
            dc_id, test_mode, auth_key, user_id, is_bot = struct.unpack(">B?256sI?", raw_bytes)
            return dc_id, api_id, test_mode, auth_key, user_id, is_bot

    # 5. Telethon IPv6 format: >B16sH256s (275 bytes)
    elif length == 275:
        dc_id, ip_bytes, port, auth_key = struct.unpack(">B16sH256s", raw_bytes)
        return dc_id, api_id, False, auth_key, 0, False

    # 6. Telethon Auth-only format: >B256s (257 bytes)
    elif length == 257:
        dc_id, auth_key = struct.unpack(">B256s", raw_bytes)
        return dc_id, api_id, False, auth_key, 0, False

    # 7. Raw 256-byte AuthKey
    elif length == 256:
        return 1, api_id, False, raw_bytes, 0, False

    # 8. Pyrogram v2 with IP/port padding (356 bytes)
    elif length == 356:
        dc_id = raw_bytes[0]
        test_mode = bool(raw_bytes[1])
        auth_key = raw_bytes[2:258]
        user_id = struct.unpack(">Q", raw_bytes[258:266])[0]
        is_bot = bool(raw_bytes[266])
        return dc_id, api_id, test_mode, auth_key, user_id, is_bot

    # 9. Scan for 256-byte auth key within buffer if length > 256
    if length > 256:
        # Check if bytes contain a SQLite header elsewhere
        idx = raw_bytes.find(b"SQLite format 3\x00")
        if idx != -1:
            res = extract_from_sqlite_bytes(raw_bytes[idx:], api_id)
            if res:
                return res

    raise ValueError(f"Unsupported session buffer length: {length} bytes (expected 271, 267, 263, 275, 257, 256, 356, or SQLite database)")


def normalize_session_string(session_str: str, default_api_id: int = 0) -> str:
    """
    Converts ANY session format (Pyrogram v1, Pyrogram v2, Telethon, Hydrogram,
    JSON metadata, base64 SQLite binary, hex auth key) into a standardized Hydrogram session string.
    """
    if not session_str or not isinstance(session_str, str):
        return session_str

    session_str = session_str.strip()
    api_id = default_api_id or get_default_api_id()

    # 1. JSON Session Check (e.g. {"session": "...", "phone": "..."} or {"auth_key": "..."})
    if session_str.startswith("{") and session_str.endswith("}"):
        try:
            data = json.loads(session_str)
            for k in ["session", "session_string", "string_session", "data", "session_str"]:
                if k in data and isinstance(data[k], str) and len(data[k]) > 20:
                    return normalize_session_string(data[k], default_api_id=api_id)
            
            # JSON with dc_id and auth_key
            if "auth_key" in data:
                ak = data["auth_key"]
                dc = int(data.get("dc_id", 1))
                if isinstance(ak, str):
                    if len(ak) == 512:
                        ak_bytes = bytes.fromhex(ak)
                    else:
                        ak_bytes = base64.b64decode(ak + "=" * (-len(ak) % 4))
                else:
                    ak_bytes = bytes(ak)
                if len(ak_bytes) == 256:
                    packed = struct.pack(">BI?256sQ?", dc, api_id, False, ak_bytes, 0, False)
                    return base64.urlsafe_b64encode(packed).decode().rstrip("=")
        except Exception:
            pass

    # 2. Hex Auth Key (512 hex characters = 256 bytes)
    if len(session_str) == 512 and all(c in "0123456789abcdefABCDEF" for c in session_str):
        try:
            ak_bytes = bytes.fromhex(session_str)
            packed = struct.pack(">BI?256sQ?", 1, api_id, False, ak_bytes, 0, False)
            return base64.urlsafe_b64encode(packed).decode().rstrip("=")
        except Exception:
            pass

    # 3. Telethon StringSession (starts with 1 and base64 encoded)
    if session_str.startswith("1") and len(session_str) > 300:
        try:
            tel_pad = "=" * (-len(session_str[1:]) % 4)
            tel_raw = base64.urlsafe_b64decode(session_str[1:] + tel_pad)
            if len(tel_raw) in (263, 275, 257):
                if len(tel_raw) == 263:  # IPv4
                    dc_id, ip_bytes, port, auth_key = struct.unpack(">B4sH256s", tel_raw)
                elif len(tel_raw) == 275:  # IPv6
                    dc_id, ip_bytes, port, auth_key = struct.unpack(">B16sH256s", tel_raw)
                else:
                    dc_id, auth_key = struct.unpack(">B256s", tel_raw)
                packed = struct.pack(">BI?256sQ?", dc_id, api_id, False, auth_key, 0, False)
                return base64.urlsafe_b64encode(packed).decode().rstrip("=")
        except Exception:
            pass

    # 4. Standard base64 / urlsafe base64 session strings or base64 SQLite databases
    try:
        pad = "=" * (-len(session_str) % 4)
        try:
            raw = base64.urlsafe_b64decode(session_str + pad)
        except Exception:
            raw = base64.b64decode(session_str + pad)

        if len(raw) == 271 and not raw.startswith(b"SQLite format 3\x00"):
            return session_str

        dc_id, api_id_val, test_mode, auth_key, user_id, is_bot = normalize_session_bytes(raw, api_id)
        packed = struct.pack(">BI?256sQ?", dc_id, api_id_val, test_mode, auth_key, user_id, is_bot)
        return base64.urlsafe_b64encode(packed).decode().rstrip("=")
    except Exception as e:
        logger.debug(f"Session string normalization fallback: {e}")
        return session_str


_patched = False

def patch_hydrogram_storage():
    """
    Monkeypatches SQLiteStorage._load_session_string so Hydrogram
    can open Pyrogram v1, Pyrogram v2, Telethon, Hydrogram, SQLite binary, and all other session formats transparently.
    """
    global _patched
    if _patched:
        return

    async def _load_session_string(self) -> None:
        if not self.conn:
            logging.warning("Database connection is not available.")
            return

        if self.session_string:
            s = self.session_string.strip()
            api_id = get_default_api_id()

            # 1. Telethon StringSession check
            if s.startswith("1") and len(s) > 300:
                try:
                    tel_pad = "=" * (-len(s[1:]) % 4)
                    tel_raw = base64.urlsafe_b64decode(s[1:] + tel_pad)
                    if len(tel_raw) in (263, 275, 257):
                        if len(tel_raw) == 263:
                            dc_id, ip_bytes, port, auth_key = struct.unpack(">B4sH256s", tel_raw)
                        elif len(tel_raw) == 275:
                            dc_id, ip_bytes, port, auth_key = struct.unpack(">B16sH256s", tel_raw)
                        else:
                            dc_id, auth_key = struct.unpack(">B256s", tel_raw)
                        await self.dc_id(dc_id)
                        await self.api_id(api_id)
                        await self.test_mode(False)
                        await self.auth_key(auth_key)
                        await self.user_id(0)
                        await self.is_bot(False)
                        await self.date(0)
                        return
                except Exception:
                    pass

            # 2. Try decoding base64 (Hydrogram, Pyrogram v1/v2, SQLite binary, raw)
            try:
                pad = "=" * (-len(s) % 4)
                try:
                    raw = base64.urlsafe_b64decode(s + pad)
                except Exception:
                    raw = base64.b64decode(s + pad)

                dc_id, api_id_val, test_mode, auth_key, user_id, is_bot = normalize_session_bytes(raw, api_id)

                await self.dc_id(dc_id)
                await self.api_id(api_id_val)
                await self.test_mode(test_mode)
                await self.auth_key(auth_key)
                await self.user_id(user_id)
                await self.is_bot(is_bot)
                await self.date(0)
            except Exception as err:
                logger.error(f"Error loading session string in Hydrogram storage: {err}")
                raise

    SQLiteStorage._load_session_string = _load_session_string
    _patched = True
    logger.info("✅ Multi-format session storage patch applied successfully.")
