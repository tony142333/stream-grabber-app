import os
import re
import subprocess
import urllib.request
from typing import Optional
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DEFAULT_QUALITIES = ["2160", "4k", "1440", "1080", "720", "480", "360"]

def upgrade_quality(stream_url: str, referer: str, cookie_header: str, quality_hierarchy: list) -> str:
    """Substitutes tokenized quality markers and verifies availability via HEAD request in priority order."""
    for target_q in quality_hierarchy:
        candidate_url = re.sub(r'([-_/])(360|480|720|1080|1440|2160)(\.mp4|p\.mp4|\?)', rf'\g<1>{target_q}\g<3>', stream_url)
        if candidate_url == stream_url and target_q in stream_url:
            return stream_url

        req = urllib.request.Request(
            candidate_url,
            headers={"Referer": referer, "Cookie": cookie_header, "User-Agent": USER_AGENT},
            method="HEAD"
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    print(f"[+] Successfully verified stream token for: {target_q}p", flush=True)
                    return candidate_url
        except Exception:
            continue
    return stream_url

def run(target_url: str, cfg_or_output: any, output_or_dir: str, download_dir_opt: Optional[str] = None):
    # Support both signatures: run(url, cfg, output_file, download_dir) and run(url, output_file, download_dir)
    if isinstance(cfg_or_output, dict):
        cfg = cfg_or_output
        output_file = output_or_dir
        download_dir = download_dir_opt or os.path.expanduser("~/downloads")
    else:
        cfg = {}
        output_file = cfg_or_output
        download_dir = output_or_dir

    quality_hierarchy = cfg.get("quality_preference", DEFAULT_QUALITIES)
    sniff_keywords = cfg.get("sniff_keywords", ["get_stream", ".mp4", ".m3u8"])
    click_selectors = cfg.get("click_selectors", [".fp-player", ".play-button", "button", "video", "[class*='play']", "#player"])

    sniffed_media_urls = set()
    session_cookies = []

    def process_url(url: str):
        if not url:
            return
        url_lower = url.lower()
        if any(kw.lower() in url_lower for kw in sniff_keywords) and "tile.vtt" not in url_lower:
            if url not in sniffed_media_urls:
                sniffed_media_urls.add(url)
                print(f"[+] Captured Token/Stream: {url}", flush=True)

    print("[*] Launching TamperDev CDP Interceptor Engine...", flush=True)

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

        cdp = page.context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.on("Network.requestWillBeSent", lambda ev: process_url(ev.get("request", {}).get("url", "")))

        page.on("request", lambda req: process_url(req.url))
        page.on("response", lambda res: process_url(res.url))

        context.on("page", lambda p_extra: p_extra.close() if p_extra != page else None)

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
                    document.querySelectorAll(sel).forEach(btn => {{
                        try {{ btn.dispatchEvent(new MouseEvent('click', {{ bubbles: true, cancelable: true, view: window }})); }} catch(e){{}}
                    }});
                }});
            }}"""

            for _ in range(12):
                if any(any(kw in u.lower() for kw in sniff_keywords) for u in sniffed_media_urls):
                    break
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
        print("[-] Error: No tokenized stream detected.", flush=True)
        return False

    referer_match = re.match(r'(https?://[^/]+)/?', target_url)
    referer = referer_match.group(0) if referer_match else target_url
    cookie_header_val = "; ".join([f"{c['name']}={c['value']}" for c in session_cookies])

    get_stream_candidates = [u for u in sniffed_media_urls if "get_stream" in u]
    selected_url = get_stream_candidates[0] if get_stream_candidates else list(sniffed_media_urls)[0]

    print(f"[*] Probing quality hierarchy: {' -> '.join(quality_hierarchy)}...", flush=True)
    target_stream_url = upgrade_quality(selected_url, referer, cookie_header_val, quality_hierarchy)

    print("=" * 60, flush=True)
    print(f"[✓] RESOLVED STREAM : {target_stream_url}", flush=True)
    print(f"[✓] COOKIES ATTACHED: {len(session_cookies)} items", flush=True)
    print(f"[✓] SAVING TO       : {download_dir}/{output_file}", flush=True)
    print("=" * 60, flush=True)

    aria2_cmd = [
        "aria2c",
        "-x", "16",
        "-s", "16",
        "-k", "1M",
        "--summary-interval=1",
        f"--header=Referer: {referer}",
        f"--header=Cookie: {cookie_header_val}",
        f"--user-agent={USER_AGENT}",
        f"--dir={download_dir}",
        "-o", output_file,
        target_stream_url
    ]

    proc = subprocess.Popen(aria2_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in iter(proc.stdout.readline, ''):
        cleaned = line.strip()
        if cleaned:
            print(cleaned, flush=True)
    proc.stdout.close()
    proc.wait()
    return True