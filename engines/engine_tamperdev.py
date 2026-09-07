import sys
import os
import re
import json
import time
import urllib.request
import urllib.error
from playwright.sync_api import sync_playwright

def verify_stream_url(url: str, referer: str, cookies: str = "") -> bool:
    """Strictly validates if a quality variant exists on CDN with 200/206 and video content."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": referer,
                "Range": "bytes=0-1024"
            }
        )
        if cookies:
            req.add_header("Cookie", cookies)

        with urllib.request.urlopen(req, timeout=4) as response:
            if response.status in (200, 206):
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" in content_type:
                    return False
                return True
    except Exception:
        return False
    return False

def probe_qualities(base_stream_url: str, referer: str, candidate_qualities: list, cookies: str = "") -> list:
    """Tests candidate resolutions against CDN and filters out nonexistent streams (phantom qualities)."""
    available = []
    print("[*] Probing for all available resolutions via CDN byte-range check...", flush=True)

    for q in candidate_qualities:
        q_str = str(q).replace("p", "").strip()
        if "{quality}" in base_stream_url:
            test_url = base_stream_url.replace("{quality}", q_str)
        else:
            test_url = re.sub(r'([-_/])(360|480|720|1080|1440|2160)(\.mp4|p\.mp4|\?)', rf'\g<1>{q_str}\g<3>', base_stream_url)

        if verify_stream_url(test_url, referer, cookies):
            print(f"[+] Verified stream variant available: {q_str}p", flush=True)
            available.append(q_str)
        else:
            print(f"[-] Variant {q_str}p not found on CDN (skipping)", flush=True)

    available.sort(key=lambda x: int(x) if x.isdigit() else 0, reverse=True)
    return available

def probe(target_url: str, output_file: str) -> bool:
    """Main entrypoint executed by get_stream.py."""
    captured_stream = None
    captured_headers = {}
    found_cookies = ""

    candidate_qualities = ["2160", "1440", "1080", "720", "480", "360"]

    print("[*] Launching Playwright browser instance...", flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )
        page = context.new_page()

        def handle_request(request):
            nonlocal captured_stream, captured_headers
            url = request.url
            if any(ext in url.lower() for ext in [".mp4", ".m3u8"]):
                if not captured_stream and not any(ign in url.lower() for ign in ["trailer", "preview", "promo", "analytics"]):
                    captured_stream = url
                    captured_headers = request.headers

        page.on("request", handle_request)

        print(f"[*] Navigating to: {target_url}", flush=True)
        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)

            raw_cookies = context.cookies()
            found_cookies = "; ".join([f"{c['name']}={c['value']}" for c in raw_cookies])

            if not captured_stream:
                selectors = [
                    ".fluid_initial_play_button",
                    "#player",
                    "button.play",
                    "button[aria-label='Play']",
                    "div.play-button",
                    "video"
                ]
                for sel in selectors:
                    try:
                        if page.locator(sel).first.is_visible():
                            page.locator(sel).first.click(timeout=2000)
                            time.sleep(2)
                            if captured_stream:
                                break
                    except Exception:
                        pass

            max_wait = 10
            while not captured_stream and max_wait > 0:
                time.sleep(1)
                max_wait -= 1

        except Exception as e:
            print(f"[-] Error during page navigation/sniff: {e}", flush=True)
        finally:
            browser.close()

    if not captured_stream:
        print("[-] Error: No stream intercepted during Playwright session", flush=True)
        return False

    print(f"[+] Captured Stream URL: {captured_stream}", flush=True)
    referer = captured_headers.get("referer", target_url)

    verified_qualities = probe_qualities(captured_stream, referer, candidate_qualities, found_cookies)

    if not verified_qualities:
        match = re.search(r'([-_/])(360|480|720|1080|1440|2160)(\.mp4|p\.mp4|\?)', captured_stream)
        if match:
            verified_qualities = [match.group(2)]
        else:
            verified_qualities = ["1080"]

    payload = {
        "base_stream": captured_stream,
        "filename": output_file,
        "referer": referer,
        "cookies": found_cookies,
        "qualities": verified_qualities
    }

    print(f"PROBE_DATA:{json.dumps(payload)}", flush=True)
    return True