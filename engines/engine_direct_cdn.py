import os
import re
import subprocess
import urllib.request
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
IGNORED_PATTERNS = ["amplifo.com", "storagexhd.com", "creative", "trailer", "preview.mp4", "tile.vtt", "main.jpg"]

def get_content_length(url: str, referer: str, cookie_header: str) -> int:
    req = urllib.request.Request(
        url,
        headers={"Referer": referer, "Cookie": cookie_header, "User-Agent": USER_AGENT},
        method="HEAD"
    )
    try:
        with urllib.request.urlopen(req, timeout=4) as resp:
            return int(resp.headers.get("Content-Length", 0))
    except Exception:
        return 0

def run(target_url: str, cfg: dict, output_filename: str, download_dir: str):
    sniff_keywords = cfg.get("sniff_keywords", [".mp4", ".m3u8"])
    click_selectors = cfg.get("click_selectors", [".fluid_initial_play_button", "video", ".play", "button", "#player"])

    sniffed_media_urls = set()
    session_cookies = []

    def process_url(url: str):
        if not url:
            return
        if any(kw.lower() in url.lower() for kw in sniff_keywords) and not any(bad in url.lower() for bad in IGNORED_PATTERNS):
            if url not in sniffed_media_urls:
                sniffed_media_urls.add(url)
                print(f"[+] Sniffed Stream URL: {url}", flush=True)

    print("[*] Launching Direct CDN / Size-Verified Engine...", flush=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-web-security",
                "--allow-running-insecure-content",
                "--autoplay-policy=no-user-gesture-required"
            ]
        )

        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            user_agent=USER_AGENT,
            ignore_https_errors=True
        )

        page = context.new_page()
        page.on("request", lambda req: process_url(req.url))
        page.on("response", lambda res: process_url(res.url))

        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)

            selector_array_js = str(click_selectors)
            click_script = f"""() => {{
                document.querySelectorAll('video').forEach(v => {{
                    try {{ v.muted = true; v.play(); }} catch(e){{}}
                }});
                const selectors = {selector_array_js};
                selectors.forEach(sel => {{
                    document.querySelectorAll(sel).forEach(el => {{
                        try {{ el.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }})); }} catch(e){{}}
                    }});
                }});
            }}"""

            for _ in range(12):
                for frame in page.frames:
                    try:
                        frame.evaluate(click_script)
                    except Exception:
                        pass
                page.wait_for_timeout(1000)

            session_cookies = context.cookies()
        except Exception as e:
            print(f"[-] Navigation notice: {e}", flush=True)
        finally:
            browser.close()

    if not sniffed_media_urls:
        print("[-] Error: No media stream detected.", flush=True)
        return False

    referer_match = re.match(r'(https?://[^/]+)/?', target_url)
    referer = referer_match.group(0) if referer_match else target_url
    cookie_header_val = "; ".join([f"{c['name']}={c['value']}" for c in session_cookies])

    print("[*] Probing candidate streams for largest video payload...", flush=True)
    best_stream_url = None
    max_bytes = 0

    for u in sniffed_media_urls:
        size = get_content_length(u, referer, cookie_header_val)
        print(f"    -> {u[:70]}... ({size / (1024*1024):.2f} MB)", flush=True)
        if size > max_bytes:
            max_bytes = size
            best_stream_url = u

    target_stream_url = best_stream_url if best_stream_url else list(sniffed_media_urls)[0]

    print("=" * 65, flush=True)
    print(f"[✓] RESOLVED STREAM : {target_stream_url}", flush=True)
    print(f"[✓] VERIFIED SIZE   : {max_bytes / (1024*1024):.2f} MB", flush=True)
    print(f"[✓] SAVING TO       : {download_dir}/{output_filename}", flush=True)
    print("=" * 65, flush=True)

    aria2_cmd = [
        "aria2c",
        "-x", "16",
        "-s", "16",
        "-k", "1M",
        "--summary-interval=1",
        f"--header=Referer: {referer}",
        f"--user-agent={USER_AGENT}",
        f"--dir={download_dir}",
        "-o", output_filename
    ]
    if cookie_header_val:
        aria2_cmd.append(f"--header=Cookie: {cookie_header_val}")
    aria2_cmd.append(target_stream_url)

    proc = subprocess.Popen(aria2_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in iter(proc.stdout.readline, ''):
        cleaned = line.strip()
        if cleaned:
            print(cleaned, flush=True)
    proc.stdout.close()
    proc.wait()
    return True