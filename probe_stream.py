import sys
import os
import re
import json
import urllib.request
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

if len(sys.argv) < 2:
    print("Usage: python3 probe_stream.py <TARGET_URL>", flush=True)
    sys.exit(1)

target_url = sys.argv[1]
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
QUALITY_VARIANTS = ["2160", "1440", "1080", "720", "480", "360"]

# Resolve Clean Base Filename
parsed_path = urlparse(target_url).path.strip("/")
last_segment = parsed_path.split("/")[-1] if parsed_path else "video"
base_name = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_segment, flags=re.IGNORECASE)
base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._") or "downloaded_video"

sniffed_urls = set()
session_cookies = []

def process_url(url: str):
    if not url:
        return
    if "get_stream" in url or ((".mp4" in url or ".m3u8" in url) and "tile.vtt" not in url):
        sniffed_urls.add(url)

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    )
    context = browser.new_context(viewport={"width": 1920, "height": 1080}, user_agent=USER_AGENT)
    page = context.new_page()

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        for _ in range(10):
            if any("get_stream" in u or ".mp4" in u for u in sniffed_urls):
                break
            for frame in page.frames:
                try:
                    frame.evaluate("""() => {
                        document.querySelectorAll('video').forEach(v => { v.muted = true; v.play(); });
                        document.querySelectorAll('.fp-player, .play-button, button, #player').forEach(b => b.click());
                    }""")
                except Exception:
                    pass
            page.wait_for_timeout(1000)
        session_cookies = context.cookies()
    except Exception as e:
        print(f"[-] Scan note: {e}", flush=True)
    finally:
        browser.close()

if not sniffed_urls:
    print("[-] Error: No stream detected.", flush=True)
    sys.exit(1)

referer_match = re.match(r'(https?://[^/]+)/?', target_url)
referer = referer_match.group(0) if referer_match else target_url
cookie_header_val = "; ".join([f"{c['name']}={c['value']}" for c in session_cookies])

# Pick base stream token
candidates = [u for u in sniffed_urls if "get_stream" in u]
base_stream = candidates[0] if candidates else list(sniffed_urls)[0]

# Probe qualities via HEAD
available_qualities = []
for q in QUALITY_VARIANTS:
    candidate_url = re.sub(r'([-_/])(360|480|720|1080|1440|2160)(\.mp4|p\.mp4|\?)', rf'\g<1>{q}\g<3>', base_stream)
    req = urllib.request.Request(
        candidate_url,
        headers={"Referer": referer, "Cookie": cookie_header_val, "User-Agent": USER_AGENT},
        method="HEAD"
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                available_qualities.append(q)
    except Exception:
        continue

if not available_qualities:
    available_qualities = ["1080"]

probe_payload = {
    "base_stream": base_stream,
    "referer": referer,
    "cookies": cookie_header_val,
    "filename": f"{base_name}.mp4",
    "qualities": available_qualities
}

print(f"PROBE_DATA:{json.dumps(probe_payload)}", flush=True)
sys.exit(0)