import asyncio
import logging
import socket
import time
import aiohttp
from datetime import datetime
from hydrogram import Client
from config import API_ID, API_HASH

logger = logging.getLogger(__name__)

PROXYSCRAPE_URL = (
    "https://api.proxyscrape.com/v4/free-proxy-list/get"
    "?request=display_proxies"
    "&protocol=socks5"
    "&anonymityLevel=elite"
    "&timeout=1000"
    "&country=all"
    "&simplified=true"
)
PROXYSCRAPE_FETCH_COUNT = 80
PROXY_REFRESH_INTERVAL  = 300
PING_TIMEOUT            = 3
MAX_PING_MS             = 1500

STATIC_PROXIES = []
PROXY_ENABLED = True  # Admins can toggle this

async def _fetch_proxyscrape() -> list:
    logger.info("📡 Fetching elite SOCKS5 proxies from ProxyScrape...")
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.get(PROXYSCRAPE_URL, timeout=aiohttp.ClientTimeout(total=15)) as r:
                text = await r.text()
        result = []
        for line in text.strip().splitlines():
            line = line.strip()
            if not line or ":" not in line:
                continue
            try:
                host, port_s = line.split(":", 1)
                result.append({
                    "scheme":   "socks5",
                    "hostname": host.strip(),
                    "port":     int(port_s.strip()),
                })
            except Exception:
                continue
        logger.info(f"ProxyScrape returned {len(result)} proxies")
        return result[:PROXYSCRAPE_FETCH_COUNT]
    except Exception as e:
        logger.error(f"ProxyScrape fetch failed: {e}")
        return []


class ProxyManager:
    def __init__(self):
        self.proxies      : list = []
        self.ping_ms      : dict = {}
        self._idx         : int  = 0
        self._lock                = asyncio.Lock()
        self._last_refresh        = None

    async def _tcp_ping(self, host: str, port: int) -> float | None:
        loop = asyncio.get_event_loop()
        try:
            start = time.monotonic()
            conn  = await asyncio.wait_for(
                loop.run_in_executor(None, lambda: socket.create_connection((host, port), timeout=PING_TIMEOUT)),
                timeout=PING_TIMEOUT + 1
            )
            conn.close()
            return round((time.monotonic() - start) * 1000, 1)
        except Exception:
            return None

    async def refresh(self):
        async with self._lock:
            fetched = await _fetch_proxyscrape()

            seen, merged = set(), []
            for p in (list(STATIC_PROXIES) + fetched):
                key = f"{p['hostname']}:{p['port']}"
                if key not in seen:
                    seen.add(key)
                    merged.append(p)

            logger.info(f"Pinging {len(merged)} proxies...")
            results  = await asyncio.gather(*[self._tcp_ping(p["hostname"], p["port"]) for p in merged])
            new_ping = {f"{p['hostname']}:{p['port']}": ms for p, ms in zip(merged, results) if ms is not None}

            alive = [
                p for p in merged
                if (new_ping.get(f"{p['hostname']}:{p['port']}") or 9999) <= MAX_PING_MS
            ]
            alive.sort(key=lambda p: new_ping.get(f"{p['hostname']}:{p['port']}") or 9999)

            self.proxies       = alive
            self.ping_ms       = new_ping
            self._idx          = 0
            self._last_refresh = datetime.utcnow()

            logger.info(f"✅ Proxy pool ready: {len(alive)}/{len(merged)} alive")
            if alive:
                b = alive[0]
                best_key = f"{b['hostname']}:{b['port']}"
                best_ms  = new_ping[best_key]
                logger.info(f"🏆 Best proxy: {best_key} → {best_ms!r}ms")

    def get_best(self) -> dict | None:
        return self.proxies[0] if self.proxies else None

    def get_next_unique(self) -> dict | None:
        if not self.proxies:
            return None
        p = self.proxies[self._idx % len(self.proxies)]
        self._idx += 1
        return p

    def format_status(self) -> str:
        global PROXY_ENABLED
        t = self._last_refresh.strftime("%H:%M:%S UTC") if self._last_refresh else "Never"
        state_icon = "🟢 ON" if PROXY_ENABLED else "🔴 OFF"
        lines = [
            "<b>🌐 PROXY POOL</b>",
            f"<b>Status:</b> {state_icon}",
            f"<i>ProxyScrape · Elite SOCKS5 · Auto-refresh 5min</i>",
            f"<b>Last refresh:</b> {t}",
            f"<b>Alive proxies:</b> {len(self.proxies)}",
            "━━━━━━━━━━━━━━━━━━━━",
        ]
        if not self.proxies:
            lines.append("⚠️ <b>No alive proxies!</b> Tap Re-fetch.")
        else:
            for i, p in enumerate(self.proxies[:12]):
                key = f"{p['hostname']}:{p['port']}"
                ms  = self.ping_ms.get(key, 0)
                dot = "🟢" if ms < 500 else "🟡" if ms < 1000 else "🔴"
                lines.append(f"{dot} <b>{i+1}.</b> <code>{key}</code> — {ms}ms")
            if len(self.proxies) > 12:
                lines.append(f"<i>...and {len(self.proxies)-12} more</i>")
            b  = self.proxies[0]
            ms = self.ping_ms.get(f"{b['hostname']}:{b['port']}", '?')
            lines.append(f"\n<b>🏆 Best now:</b> <code>{b['hostname']}:{b['port']}</code> ({ms}ms)")
        return "\n".join(lines)

    async def start_auto_refresh(self):
        while True:
            await asyncio.sleep(PROXY_REFRESH_INTERVAL)
            logger.info("⏰ Auto-refresh: fetching fresh proxies from ProxyScrape...")
            await self.refresh()


proxy_manager = ProxyManager()

def toggle_proxy():
    global PROXY_ENABLED
    PROXY_ENABLED = not PROXY_ENABLED
    return PROXY_ENABLED

def set_proxy(state: bool):
    global PROXY_ENABLED
    PROXY_ENABLED = state

def is_proxy_enabled():
    global PROXY_ENABLED
    return PROXY_ENABLED

def build_client(name: str, proxy: dict | None = None, **kwargs) -> Client:
    """
    Safe Client factory — NEVER passes proxy=None (hydrogram crashes).
    If proxy is None, falls back to best alive proxy.
    """
    global PROXY_ENABLED
    kw = dict(name=name, api_id=API_ID, api_hash=API_HASH, **kwargs)
    if PROXY_ENABLED:
        p = proxy if proxy is not None else proxy_manager.get_best()
        if p:
            kw["proxy"] = p
    return Client(**kw)
