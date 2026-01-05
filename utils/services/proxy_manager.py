diff --git a/utils/services/proxy_manager.py b/utils/services/proxy_manager.py
new file mode 100644
index 0000000..e69de29
--- /dev/null
+++ b/utils/services/proxy_manager.py
@@ -0,0 +1,238 @@
+import os
+import sys
+import ssl
+import json
+import asyncio
+from typing import List, Tuple, Optional, Any
+from urllib.parse import urlparse, urlunparse
+import aiohttp
+
+from utils.settings import logger, Fore
+
+# Optional socks support (install aiohttp_socks if you need socks proxies)
+try:
+    from aiohttp_socks import ProxyConnector  # type: ignore
+    _HAS_AIOHTTP_SOCKS = True
+except Exception:
+    _HAS_AIOHTTP_SOCKS = False
+
+PROXIES_FILE = "proxies.txt"
+TOKENS_FILE = "tokens.txt"
+
+
+def _mask_proxy(proxy: str) -> str:
+    """Return a masked version of a proxy for safe logging."""
+    try:
+        p = urlparse(proxy)
+        host = p.hostname or "unknown"
+        port = p.port or ""
+        user = p.username or ""
+        if user:
+            return f"{user[:1]}***@{host}:{port}"
+        return f"{host}:{port}"
+    except Exception:
+        return "masked-proxy"
+
+
+def load_proxies(path: str = PROXIES_FILE) -> List[str]:
+    """
+    Load proxies from a file. Each non-empty line is a proxy string.
+    Strips surrounding quotes and whitespace.
+    """
+    try:
+        with open(path, "r", encoding="utf-8") as f:
+            lines = [ln.strip() for ln in f.read().splitlines()]
+        proxies = [ln.strip('"').strip("'") for ln in lines if ln and not ln.isspace()]
+        if not proxies:
+            logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}No proxies found in {path}. Running without proxies{Fore.RESET}")
+        return proxies
+    except FileNotFoundError:
+        logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}File {path} not found. Running without proxies{Fore.RESET}")
+        return []
+    except Exception as e:
+        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Error loading proxies:{Fore.RESET} {e}")
+        return []
+
+
+def _env_choice_to_bool(value: Optional[str]) -> Optional[bool]:
+    if value is None:
+        return None
+    v = value.strip().lower()
+    if v in ("yes", "y", "true", "1"):
+        return True
+    if v in ("no", "n", "false", "0"):
+        return False
+    return None
+
+
+def get_proxy_choice() -> List[str]:
+    """
+    Determine whether to use proxies and return the list of proxies to use.
+    Priority:
+      1. Environment variable USE_PROXY (yes/no)
+      2. Command-line flags --use-proxy / --no-proxy
+      3. Interactive prompt (if TTY)
+      4. Default: no proxies
+    """
+    # 1) Env var
+    env_choice = _env_choice_to_bool(os.getenv("USE_PROXY"))
+    if env_choice is True:
+        return load_proxies()
+    if env_choice is False:
+        return []
+
+    # 2) CLI flags
+    if "--use-proxy" in sys.argv:
+        return load_proxies()
+    if "--no-proxy" in sys.argv:
+        return []
+
+    # 3) Interactive fallback
+    try:
+        if sys.stdin is not None and sys.stdin.isatty():
+            while True:
+                user_input = input("Do you want to use proxy? (yes/no)? ").strip().lower()
+                if user_input in ("yes", "no"):
+                    break
+                print("Invalid input. Please enter 'yes' or 'no'.")
+            if user_input == "yes":
+                return load_proxies()
+            return []
+    except Exception:
+        # Non-interactive environment
+        pass
+
+    # 4) Default
+    logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Non-interactive environment and no proxy preference set. Running without proxies{Fore.RESET}")
+    return []
+
+
+def assign_proxies(tokens: List[str], proxies: Optional[List[str]]) -> List[Tuple[str, Optional[str]]]:
+    """
+    Pair tokens with proxies. If there are fewer proxies than tokens,
+    remaining tokens are paired with None.
+    """
+    if proxies is None:
+        proxies = []
+    paired = list(zip(tokens[: len(proxies)], proxies))
+    remaining = [(token, None) for token in tokens[len(proxies) :]]
+    return paired + remaining
+
+
+def get_proxy_ip(proxy_url: Optional[str]) -> str:
+    """Extract hostname from proxy URL or return 'Unknown'."""
+    try:
+        if not proxy_url:
+            return "Unknown"
+        return urlparse(proxy_url).hostname or "Unknown"
+    except Exception:
+        return "Unknown"
+
+
+def create_ssl_context() -> ssl.SSLContext:
+    """Create an SSL context that does not verify certificates (useful for self-signed)."""
+    ctx = ssl.create_default_context()
+    ctx.check_hostname = False
+    ctx.verify_mode = ssl.CERT_NONE
+    return ctx
+
+
+async def get_ip_address(proxy: Optional[str] = None, timeout: int = 10) -> str:
+    """
+    Get public IP address optionally through a proxy.
+    Handles HTTP(S) proxies with proxy_auth and SOCKS proxies if aiohttp_socks is installed.
+    Returns the IP string or 'Unknown' on failure.
+    """
+    proxy_ip = "Unknown"
+    try:
+        # Clean proxy
+        if proxy:
+            proxy = proxy.strip()
+            parsed = urlparse(proxy)
+            if not parsed.scheme or not parsed.hostname or not parsed.port:
+                logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Invalid proxy format: {proxy}{Fore.RESET}")
+                proxy = None
+            else:
+                proxy_ip = parsed.hostname
+
+        url = "https://api.ipify.org?format=json"
+        ssl_context = create_ssl_context()
+
+        # If no proxy, simple request
+        if not proxy:
+            async with aiohttp.ClientSession() as session:
+                async with session.get(url, ssl=ssl_context, timeout=timeout) as resp:
+                    if resp.status == 200:
+                        data = await resp.json()
+                        return data.get("ip", "Unknown")
+                    logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Request returned status {resp.status}{Fore.RESET}")
+                    return "Unknown"
+
+        # Proxy present: parse and prepare
+        parsed = urlparse(proxy)
+        scheme = parsed.scheme.lower()
+
+        # If SOCKS and aiohttp_socks available, use ProxyConnector
+        if scheme.startswith("socks"):
+            if not _HAS_AIOHTTP_SOCKS:
+                logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}SOCKS proxy requested but aiohttp_socks is not installed{Fore.RESET}")
+                return proxy_ip
+            # ProxyConnector.from_url accepts full URL with credentials
+            connector = ProxyConnector.from_url(proxy)
+            async with aiohttp.ClientSession(connector=connector) as session:
+                async with session.get(url, ssl=ssl_context, timeout=timeout) as resp:
+                    if resp.status == 200:
+                        data = await resp.json()
+                        return data.get("ip", "Unknown")
+                    logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Proxy request returned status {resp.status}{Fore.RESET}")
+                    return "Unknown"
+
+        # HTTP(S) proxy: separate credentials from proxy URL
+        proxy_no_auth = urlunparse((parsed.scheme, f"{parsed.hostname}:{parsed.port}", "", "", "", ""))
+        proxy_auth = None
+        if parsed.username or parsed.password:
+            from aiohttp import BasicAuth
+            proxy_auth = BasicAuth(parsed.username or "", parsed.password or "")
+
+        async with aiohttp.ClientSession() as session:
+            async with session.get(url, proxy=proxy_no_auth, proxy_auth=proxy_auth, ssl=ssl_context, timeout=timeout) as resp:
+                if resp.status == 200:
+                    data = await resp.json()
+                    return data.get("ip", "Unknown")
+                logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Proxy request returned status {resp.status}{Fore.RESET}")
+                return "Unknown"
+
+    except Exception as e:
+        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Request failed via proxy {proxy_ip}:{Fore.RESET} {e}")
+    return proxy_ip
+
+
+async def resolve_ip(account: Any) -> str:
+    """
+    Resolve IP for an account object that may have a .proxy attribute.
+    Returns the resolved IP or 'Unknown' on failure.
+    """
+    try:
+        acct_proxy = getattr(account, "proxy", None)
+        if acct_proxy and isinstance(acct_proxy, str) and acct_proxy.startswith(("http", "socks")):
+            return await get_ip_address(acct_proxy)
+        return await get_ip_address()
+    except Exception as e:
+        try:
+            idx = getattr(account, "index", 0)
+            logger.error(f"{Fore.CYAN}{idx:02d}{Fore.RESET} - {Fore.RED}Failed to resolve proxy or IP address:{Fore.RESET} {e}")
+        except Exception:
+            logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Failed to resolve proxy or IP address:{Fore.RESET} {e}")
+        return "Unknown"
+
