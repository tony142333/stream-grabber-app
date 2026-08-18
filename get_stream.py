import sys
import re
import os
import subprocess
from urllib.parse import urlparse
import urllib.request
from playwright.sync_api import sync_playwright

from modules.config_manager.config_core import ConfigEngine

if len(sys.argv) < 2:
    print("Usage: python3 get_stream.py <STREAMING_PAGE_URL>", flush=True)
    sys.exit(1)

target_url = sys.argv[1]

# Initialize Config Matcher Engine
engine = ConfigEngine()
cfg = engine.match_url(target_url)
print(f"[+] Matched site profile: {cfg.get('name', 'Default')}", flush=True)

# Extraction settings from config profile arrays
sniff_keywords = cfg.get("sniff_keywords", [".mp4", ".m3u8"])
quality_variants = cfg.get("quality_preference", ["2160", "4k", "1440", "1080", "720", "480", "360"])
click_selectors = cfg.get("click_selectors", ["video", "button"])
title_mode = cfg.get("title_mode", "page_url")
title_pattern = cfg.get("title_pattern", "")

download_dir = os.path.expanduser("~/downloads")
os.makedirs(download_dir, exist_ok=True)

sniffed_media_urls = set()
base_cdn_paths = set()
extracted_html_title = None

def extract_fallback_name(url: str) -> str:
    parsed_path = urlparse(url).path.strip("/")
    last_segment = parsed_path.split("/")[-1] if parsed_path else "video"
    name = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_segment, flags=re.IGNORECASE)
    name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', name).strip("._")
    return name if name else "downloaded_video"

def probe_highest_quality(base_url: str, referer: str) -> str:
    """Probes HEAD requests for highest available quality on the stream/CDN path."""
    for q in quality_variants:
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
                    return test_url
        except Exception:
            continue
    return f"{base_url}/1080.mp4"

def process_url(url: str):
    if not url:
        return

    if any(kw.lower() in url.lower() for kw in sniff_keywords):
        if any(q in url for q in quality_variants) or url.endswith(".mp4") or ".m3u8" in url or "manifest" in url:
            if url not in sniffed_media_urls:
                sniffed_media_urls.add(url)
                print(f"[+] Sniffed Stream URL: {url}", flush=True)
        elif "tile.vtt" in url or "main.jpg" in url or "preview.mp4" in url:
            base = url.rsplit("/", 1)[0]
            base_cdn_paths.add(base)

print(f"[*] Navigating to: {target_url}", flush=True)

with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-web-security",
            "--allow-running-insecure-content"
        ]
    )

    context = browser.new_context(
        viewport={"width": 1920, "height": 1080},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        permissions=["autoplay"],
        ignore_https_errors=True
    )

    page = context.new_page()

    # Dynamic Engine Mode Evaluation
    engine_mode = cfg.get("engine_mode", "standard")

    if engine_mode == "tamperdev":
        print("[*] Activating CDP Protocol Interceptor (TamperDev Emulation Core)...", flush=True)
        page.add_init_script("""
            const _fetch = window.fetch;
            window.fetch = async function(...args) {
                const url = args[0] ? (typeof args[0] === 'string' ? args[0] : args[0].url) : '';
                if (url) console.log('__TAMPERDEV_URL__:' + url);
                return _fetch.apply(this, args);
            };

            const _open = XMLHttpRequest.prototype.open;
            XMLHttpRequest.prototype.open = function(method, url) {
                if (url) console.log('__TAMPERDEV_URL__:' + url);
                return _open.apply(this, arguments);
            };
        """)

        page.on("console", lambda msg: process_url(msg.text.replace("__TAMPERDEV_URL__:", "")) if "__TAMPERDEV_URL__:" in msg.text else None)

        cdp_session = page.context.new_cdp_session(page)
        cdp_session.send("Network.enable")
        cdp_session.on("Network.requestWillBeSent", lambda event: process_url(event.get("request", {}).get("url", "")))
    else:
        print("[*] Activating Standard Lifecycle Sniffer...", flush=True)

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    context.on("page", lambda p: p.close() if p != page else None)

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

        # Extract Title
        if title_mode == "html_tag" and title_pattern:
            try:
                el = page.query_selector(title_pattern)
                if el:
                    extracted_html_title = el.inner_text().strip()
            except Exception:
                pass

        # Interaction & Click Loop
        selector_array_js = str(click_selectors)
        interaction_script = f"""() => {{
            document.querySelectorAll('video').forEach(v => {{
                try {{ v.muted = true; v.play(); }} catch(e){{}}
            }});
            const selectors = {selector_array_js};
            selectors.forEach(sel => {{
                document.querySelectorAll(sel).forEach(b => {{
                    try {{
                        b.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }}));
                    }} catch(e){{}}
                }});
            }});
        }}"""

        for _ in range(15):
            if sniffed_media_urls or base_cdn_paths:
                break
            for frame in page.frames:
                try:
                    frame.evaluate(interaction_script)
                except Exception:
                    pass
            page.wait_for_timeout(1000)

    except Exception as e:
        print(f"[-] Navigation note: {e}", flush=True)
    finally:
        browser.close()

# Resolve Final Target & File Name
base_name = None
if title_mode == "html_tag" and extracted_html_title:
    base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', extracted_html_title).strip("._")
elif title_mode == "page_url" and title_pattern:
    match = re.search(title_pattern, target_url)
    if match:
        base_name = match.group(1) if match.groups() else match.group(0)
        base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._")

if not base_name:
    base_name = extract_fallback_name(target_url)

output_file = f"{base_name}.mp4"

target_stream_url = None
primary_cdn_base = None

if sniffed_media_urls:
    for q in quality_variants:
        matched = [u for u in sniffed_media_urls if f"/{q}.mp4" in u or f"_{q}p" in u or f"/{q}/" in u]
        if matched:
            target_stream_url = matched[0]
            primary_cdn_base = target_stream_url.rsplit("/", 1)[0]
            break

    if not target_stream_url and sniffed_media_urls:
        target_stream_url = list(sniffed_media_urls)[0]
        primary_cdn_base = target_stream_url.rsplit("/", 1)[0]
elif base_cdn_paths:
    primary_cdn_base = list(base_cdn_paths)[0]

if not target_stream_url and primary_cdn_base:
    target_stream_url = f"{primary_cdn_base}/1080.mp4"

if not target_stream_url:
    print("[-] Error: No matching stream or CDN path detected.", flush=True)
    sys.exit(1)

referer_match = re.match(r'(https?://[^/]+)/?', target_url)
referer = referer_match.group(0) if referer_match else "https://google.com/"

if primary_cdn_base and not target_stream_url.endswith(".m3u8") and "index" not in target_stream_url:
    print(f"[*] Probing CDN path for highest resolution hierarchy...", flush=True)
    target_stream_url = probe_highest_quality(primary_cdn_base, referer)

print("=" * 60, flush=True)
print(f"HIGHEST QUALITY DETECTED: {target_stream_url}", flush=True)
print(f"SAVING TO: {download_dir}/{output_file}", flush=True)
print("=" * 60, flush=True)

aria2_cmd = [
    "aria2c",
    "-x", "16",
    "-s", "16",
    "-k", "1M",
    "--summary-interval=1",
    f"--header=Referer: {referer}",
    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    f"--dir={download_dir}",
    "-o", output_file,
    target_stream_url
]

proc = subprocess.Popen(
    aria2_cmd,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    bufsize=1
)

for line in iter(proc.stdout.readline, ''):
    cleaned = line.strip()
    if cleaned:
        print(cleaned, flush=True)

proc.stdout.close()
proc.wait()