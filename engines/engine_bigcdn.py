import os
import re
import json
import urllib.request
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

QUALITY_VARIANTS = ["2160", "4k", "1440", "1080", "720", "480", "360"]

def probe_available_qualities(base_url: str, referer: str) -> list[str]:
    available = []
    for q in QUALITY_VARIANTS:
        test_url = f"{base_url}/{q}.mp4"
        req = urllib.request.Request(
            test_url,
            headers={
                "Referer": referer,
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            },
            method="HEAD"
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    print(f"[+] Verified available stream: {q}p -> {test_url}", flush=True)
                    available.append(q)
        except Exception:
            continue
    return available if available else ["1080"]

def probe(target_url: str, output_file: str):
    sniffed_mp4_urls = set()
    base_cdn_paths = set()

    print(f"[*] Navigating to: {target_url}", flush=True)

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
        page = browser.new_page(
            viewport={"width": 1920, "height": 1080},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        )

        def process_url(url: str):
            if not url or "bigcdn.cc" not in url:
                return

            if re.search(r'/(2160|4k|1440|1080|720|480|360)\.mp4', url):
                sniffed_mp4_urls.add(url)
                print(f"[+] Sniffed MP4 URL: {url}", flush=True)
            elif "tile.vtt" in url or "main.jpg" in url or "preview.mp4" in url:
                base = url.rsplit("/", 1)[0]
                base_cdn_paths.add(base)

        page.on("request", lambda req: process_url(req.url))
        page.on("response", lambda res: process_url(res.url))

        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

            for _ in range(12):
                if sniffed_mp4_urls or base_cdn_paths:
                    break
                for frame in page.frames:
                    try:
                        frame.evaluate("""() => {
                            document.querySelectorAll('video').forEach(v => { v.muted = true; v.play(); });
                            document.querySelectorAll('.fluid_initial_play_button, .play, button, #player').forEach(b => b.click());
                        }""")
                    except Exception:
                        pass
                page.wait_for_timeout(1000)

        except Exception as e:
            print(f"[-] Navigation note: {e}", flush=True)
        finally:
            browser.close()

    primary_cdn_base = None
    if sniffed_mp4_urls:
        for q in QUALITY_VARIANTS:
            matched = [u for u in sniffed_mp4_urls if f"/{q}.mp4" in u]
            if matched:
                primary_cdn_base = matched[0].rsplit("/", 1)[0]
                break
        if not primary_cdn_base:
            primary_cdn_base = list(sniffed_mp4_urls)[0].rsplit("/", 1)[0]
    elif base_cdn_paths:
        primary_cdn_base = list(base_cdn_paths)[0]

    if not primary_cdn_base:
        print("[-] Error: No matching BigCDN stream or path found.", flush=True)
        return False

    referer_match = re.search(r'(https?://[^/]+bigcdn\.cc)/', primary_cdn_base)
    referer = f"{referer_match.group(1)}/" if referer_match else "https://s6.bigcdn.cc/"

    print("[*] Probing CDN path for all available resolutions...", flush=True)
    working_qualities = probe_available_qualities(primary_cdn_base, referer)

    base_template = f"{primary_cdn_base}/{{quality}}.mp4"

    payload = {
        "base_stream": base_template,
        "referer": referer,
        "cookies": "",
        "filename": output_file,
        "qualities": working_qualities
    }

    print(f"PROBE_DATA:{json.dumps(payload)}", flush=True)
    return True