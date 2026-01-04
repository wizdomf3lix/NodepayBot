import os
import sys
import ssl
import aiohttp

from typing import List, Tuple, Optional
from urllib.parse import urlparse
from utils.settings import logger, Fore


PROXIES_FILE = "proxies.txt"


def load_proxies() -> List[str]:
    """
    Load proxies from PROXIES_FILE. Returns a list of proxy strings.
    Logs warnings/errors but never prints secrets.
    """
    try:
        with open(PROXIES_FILE, "r", encoding="utf-8") as f:
            proxies = [line.strip() for line in f.read().splitlines() if line.strip()]

        if not proxies:
            logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}No proxies found in {PROXIES_FILE}. Running without proxies{Fore.RESET}")

        return proxies

    except FileNotFoundError:
        logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}File {PROXIES_FILE} not found. Running without proxies{Fore.RESET}")
        return []

    except Exception as e:
        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Error loading proxies:{Fore.RESET} {e}")
        return []


def _env_choice_to_bool(value: str) -> Optional[bool]:
    if value is None:
        return None
    v = value.strip().lower()
    if v in ("yes", "y", "true", "1"):
        return True
    if v in ("no", "n", "false", "0"):
        return False
    return None


def get_proxy_choice() -> List[str]:
    """
    Determine whether to use proxies and return the list of proxies to use.
    Priority:
      1. Environment variable USE_PROXY (yes/no, y/n, true/false, 1/0)
      2. Command-line flags --use-proxy / --no-proxy
      3. Interactive prompt fallback (only when a TTY is available)
      4. Non-interactive default: do not use proxies
    """
    # 1) Environment variable
    env_val = os.getenv("USE_PROXY")
    env_bool = _env_choice_to_bool(env_val)
    if env_bool is True:
        proxies = load_proxies()
        if not proxies:
            logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}No proxies found in {PROXIES_FILE}. Please add valid proxies{Fore.RESET}")
            return []
        return proxies
    if env_bool is False:
        return []

    # 2) Command-line flags
    if "--use-proxy" in sys.argv:
        proxies = load_proxies()
        if not proxies:
            logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}No proxies found in {PROXIES_FILE}. Please add valid proxies{Fore.RESET}")
            return []
        return proxies
    if "--no-proxy" in sys.argv:
        return []

    # 3) Interactive fallback (only if stdin is a TTY)
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            while True:
                user_input = input("Do you want to use proxy? (yes/no)? ").strip().lower()
                if user_input in ("yes", "no"):
                    break
                print("Invalid input. Please enter 'yes' or 'no'.")
            logger.info(f"You selected: {'Yes' if user_input == 'yes' else 'No'}, ENJOY!\n")
            if user_input == "yes":
                proxies = load_proxies()
                if not proxies:
                    logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}No proxies found in {PROXIES_FILE}. Please add valid proxies{Fore.RESET}")
                    return []
                return proxies
            return []
    except Exception:
        # If any issue with interactive input, fall through to non-interactive default
        pass

    # 4) Non-interactive default
    logger.warning(f"{Fore.CYAN}00{Fore.RESET} - {Fore.YELLOW}Non-interactive environment detected and no proxy preference set. Running without proxies{Fore.RESET}")
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


def get_proxy_ip(proxy_url: Optional[str]) -> str:
    """
    Extract hostname from proxy URL. Returns 'Unknown' on failure.
    """
    try:
        if not proxy_url:
            return "Unknown"
        return urlparse(proxy_url).hostname or "Unknown"
    except Exception:
        return "Unknown"


def create_ssl_context() -> ssl.SSLContext:
    """
    Create an SSL context that does not verify certificates (useful for self-signed).
    """
    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE
    return ssl_context


async def get_ip_address(proxy: Optional[str] = None) -> str:
    """
    Get public IP address optionally through a proxy.
    Returns the IP string or 'Unknown' on failure.
    """
    proxy_ip = get_proxy_ip(proxy) if proxy else "Unknown"
    url = "https://api.ipify.org?format=json"
    ssl_context = create_ssl_context()

    try:
        async with aiohttp.ClientSession() as session:
            # aiohttp expects the proxy argument to be a string like "http://user:pass@host:port"
            async with session.get(url, proxy=proxy, ssl=ssl_context) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get("ip", "Unknown")
                return "Unknown"
    except Exception:
        logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Request failed: Server disconnected{Fore.RESET}")
        return proxy_ip


async def resolve_ip(account) -> str:
    """
    Resolve IP for an account object that may have a .proxy attribute.
    Returns the resolved IP or 'Unknown' on failure.
    """
    try:
        if getattr(account, "proxy", None) and str(account.proxy).startswith("http"):
            return await get_ip_address(account.proxy)
        else:
            return await get_ip_address()
    except Exception as e:
        try:
            idx = getattr(account, "index", 0)
            logger.error(f"{Fore.CYAN}{idx:02d}{Fore.RESET} - {Fore.RED}Failed to resolve proxy or IP address:{Fore.RESET} {e}")
        except Exception:
            logger.error(f"{Fore.CYAN}00{Fore.RESET} - {Fore.RED}Failed to resolve proxy or IP address:{Fore.RESET} {e}")
        return "Unknown"
