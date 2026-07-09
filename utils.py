import os
import sys
import pathlib
from concurrent.futures import as_completed

import requests
from requests_futures.sessions import FuturesSession
from tqdm import tqdm


def _filter_proxy_values(values) -> list:
    return [value for value in values if value and ":" in value]


def clear_screen() -> None:
    if os.name == "nt":
        os.system("cls")
    elif os.getenv("TERM"):
        os.system("clear")


def _read_cached_proxies() -> list:
    if not pathlib.Path("proxies.txt").exists():
        return []

    with open("proxies.txt") as f:
        return _filter_proxy_values(f.read().splitlines())


def get_working_proxies(refresh: bool = False):

    if not refresh:
        cached_proxies = _read_cached_proxies()
        if cached_proxies:
            return [None] + cached_proxies

    proxies = []

    print("No proxies found, fetching proxies from api.proxyscrape.com...")
    try:
        r = requests.get(
            "https://api.proxyscrape.com/?request=getproxies&proxytype=https&timeout=10000&country=all&ssl=all&anonymity=all",
            timeout=15,
        )
        proxies += r.text.splitlines()
        r = requests.get(
            "https://api.proxyscrape.com/?request=getproxies&proxytype=http&timeout=10000&country=all&ssl=all&anonymity=all",
            timeout=15,
        )
        proxies += r.text.splitlines()
    except requests.RequestException as exc:
        print(f"Proxy fetch failed: {exc}. Falling back to direct connection only.")
        return [None]

    proxies = _filter_proxy_values(proxies)
    working_proxies = []
    print(f"Checking {len(proxies)} proxies...")

    if not proxies:
        return [None]

    # Empty proxy lists return above, so max_workers is always at least 1 here.
    session = FuturesSession(max_workers=min(100, len(proxies)))
    futures = []
    
    for proxy in proxies:
        future = session.get('https://api.myip.com', proxies={'https': f'http://{proxy}'}, timeout=5)
        future.proxy = proxy
        futures.append(future)

    for future in tqdm(as_completed(futures), total=len(futures)):#, disable=True):
        try:
            future.result()
            working_proxies.append(future.proxy)
        except KeyboardInterrupt:
            sys.exit()
        except:
            continue

    with open("proxies.txt", "w") as f:
        f.write("\n".join(working_proxies))

    clear_screen()

    return [None] + working_proxies
