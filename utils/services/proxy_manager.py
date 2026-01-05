import os
import ssl
import asyncio
from typing import List, Optional, Tuple, Any
from urllib.parse import urlparse, urlunparse
import aiohttp

# Optional socks support
try:
    from aiohttp_socks import ProxyConnector  # type: ignore
    _HAS_AIOHTTP_SOCKS = True
except Exception:
    _HAS_AIOHTTP_SOCKS = False

# Replace these imports with your project's logger and color constants if needed
try:
    from utils.settings import logger, Fore
except Exception:
    # Minimal fallback logger if utils.settings is not available
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("proxy_manager")
    class Fore:
        CYAN = ""
        YELLOW = ""
        RED = ""
        RESET = ""

PROXIES_FILE = "proxies.txt"
TOKENS_FILE = "tokens.txt"


def _mask_proxy(proxy: str) -> str:
    """Return a masked version of a proxy for safe logging."""
    try:
        p = urlparse(proxy)
        host = p.hostname or "unknown"
        port = p.port or ""
        user = p.username or ""
        if user:
            return f"{user[:1]}***@{host}:{port}"
        return f"{host}:{port}"
    except Exception:
        return "masked-proxy"


def load_proxies(path: str = PROXIES_FILE) -> List[str]:
    """
    Load proxies from a file. Each non-empty line is a proxy string.
    Strips surrounding quotes and whitespace.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
        proxies = [ln.strip('"').strip("'") for ln in lines if ln and not ln.isspace()]
        if not proxies:
            logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}No proxies found in {path}. Running without proxies{Fore.RESET}")
        else:
            logger.info(f"{Fore.CYAN}00{Fore.RESET} - Loaded {len(proxies)} proxies (masked): {', '.join(_mask_proxy(p) for p in proxies)}")
        return proxies
    except FileNotFoundError:
        logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}File {path} not found. Running without proxies{Fore.RESET}")
        return []
    except Exception as e:
        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Error loading proxies:{Fore.RESET} {e}")
        return []


def load_tokens(path: str = TOKENS_FILE) -> List[str]:
    """Load tokens from a file, one per line. Strips whitespace."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines()]
        tokens = [ln for ln in lines if ln]
        logger.info(f"{Fore.CYAN}00{Fore.RESET} - Loaded {len(tokens)} tokens")
        return tokens
    except FileNotFoundError:
        logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}File {path} not found. No tokens loaded{Fore.RESET}")
        return []
    except Exception as e:
        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Error loading tokens:{Fore.RESET} {e}")
        return []


def assign_proxies(tokens: List[str], proxies: Optional[List[str]]) -> List[Tuple[str, Optional[str]]]:
    """
    Pair tokens with proxies. If there are fewer proxies than tokens,
    remaining tokens are paired with None.
    """
    if proxies is None:
        proxies = []
    paired = list(zip(tokens[: len(proxies)], proxies))
    remaining = [(token, None) for token in tokens[len(proxies) :]]
    return paired + remaining


def create_ssl_context() -> ssl.SSLContext:
    """Create an SSL context that does not verify certificates (useful for self-signed)."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _fetch_with_retries(session: aiohttp.ClientSession, url: str, **kwargs) -> aiohttp.ClientResponse:
    """Simple retry wrapper with exponential backoff."""
    attempts = kwargs.pop("_attempts", 3)
    backoff = kwargs.pop("_backoff", 1)
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            resp = await session.get(url, **kwargs)
            return resp
        except Exception as e:
            last_exc = e
            logger.warning(f"{Fore.CYAN}00{Fore.RESET} - Attempt {attempt} failed: {type(e).__name__} {e}")
            if attempt < attempts:
                await asyncio.sleep(backoff * attempt)
    raise last_exc


async def get_ip_address(proxy: Optional[str] = None, timeout: int = 10) -> str:
    """
    Get public IP address optionally through a proxy.
    Supports:
      - HTTP(S) proxies with credentials via proxy_auth
      - SOCKS proxies if aiohttp_socks is installed
    Returns the IP string or 'Unknown' on failure.
    """
    proxy_ip = "Unknown"
    try:
        # Clean proxy
        if proxy:
            proxy = proxy.strip()
            parsed = urlparse(proxy)
            if not parsed.scheme or not parsed.hostname or not parsed.port:
                logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Invalid proxy format: {proxy}{Fore.RESET}")
                proxy = None
            else:
                proxy_ip = parsed.hostname

        url = "https://api.ipify.org?format=json"
        ssl_context = create_ssl_context()

        # No proxy: direct request
        if not proxy:
            async with aiohttp.ClientSession() as session:
                resp = await _fetch_with_retries(session, url, ssl=ssl_context, timeout=timeout)
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("ip", "Unknown")
                logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Request returned status {resp.status}{Fore.RESET}")
                return "Unknown"

        # Proxy present: parse and prepare
        parsed = urlparse(proxy)
        scheme = parsed.scheme.lower()

        # SOCKS proxy handling
        if scheme.startswith("socks"):
            if not _HAS_AIOHTTP_SOCKS:
                logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}SOCKS proxy requested but aiohttp_socks is not installed{Fore.RESET}")
                return proxy_ip
            connector = ProxyConnector.from_url(proxy)
            async with aiohttp.ClientSession(connector=connector) as session:
                resp = await _fetch_with_retries(session, url, ssl=ssl_context, timeout=timeout)
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("ip", "Unknown")
                logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Proxy request returned status {resp.status}{Fore.RESET}")
                return "Unknown"

        # HTTP(S) proxy: separate credentials from proxy URL
        proxy_no_auth = urlunparse((parsed.scheme, f"{parsed.hostname}:{parsed.port}", "", "", "", ""))
        proxy_auth = None
        if parsed.username or parsed.password:
            from aiohttp import BasicAuth
            proxy_auth = BasicAuth(parsed.username or "", parsed.password or "")

        async with aiohttp.ClientSession() as session:
            resp = await _fetch_with_retries(session, url, proxy=proxy_no_auth, proxy_auth=proxy_auth, ssl=ssl_context, timeout=timeout)
            if resp.status == 200:
                data = await resp.json()
                return data.get("ip", "Unknown")
            logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Proxy request returned status {resp.status}{Fore.RESET}")
            return "Unknown"

    except Exception as e:
        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Request failed via proxy {proxy_ip}:{Fore.RESET} {e}")
    return proxy_ip


async def resolve_ip_for_account(account: Any) -> str:
    """
    Resolve IP for an account object that may have a .proxy attribute.
    Returns the resolved IP or 'Unknown' on failure.
    """
    try:
        acct_proxy = getattr(account, "proxy", None)
        if acct_proxy and isinstance(acct_proxy, str) and acct_proxy.startswith(("http", "socks")):
            return await get_ip_address(acct_proxy)
        return await get_ip_address()
    except Exception as e:
        try:
            idx = getattr(account, "index", 0)
            logger.error(f"{Fore.CYAN}{idx:02d}{Fore.RESET} - {Fore.RED}Failed to resolve proxy or IP address:{Fore.RESET} {e}")
        except Exception:
            logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Failed to resolve proxy or IP address:{Fore.RESET} {e}")
        return "Unknown"


# Optional helper: synchronous wrapper for quick checks
def check_proxy_sync(proxy: Optional[str] = None, timeout: int = 10) -> str:
    """Synchronous convenience wrapper to call get_ip_address from non-async code."""
    try:
        return asyncio.run(get_ip_address(proxy=proxy, timeout=timeout))
    except Exception as e:
        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Synchronous proxy check failed:{Fore.RESET} {e}")
        return "Unknown"


# If run as a script, perform a quick test using proxies.txt
if __name__ == "__main__":
    proxies = load_proxies()
    if not proxies:
        print("No proxies found in proxies.txt")
    else:
        print("Testing proxies (masked):", ", ".join(_mask_proxy(p) for p in proxies))
        async def _run_tests():
            for p in proxies:
                ip = await get_ip_address(p)
                print(f"{_mask_proxy(p)} -> {ip}")
        asyncio.run(_run_tests())
