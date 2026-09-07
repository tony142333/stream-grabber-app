import os
import re
import json
import urllib.request
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
QUALITY_VARIANTS = ["2160", "4k", "1440", "1080", "720", "480", "360"]

def probe_available_qualities(stream_url: str, referer: str, cookie_header: str) -> list[str]:
    """Tests HEAD requests for all quality tokens and returns an ordered list of verified resolutions."""
    available = []
    for target_q in QUALITY_VARIANTS:
        candidate_url = re.sub(
            r'([-_/])(360|480|720|1080|1440|2160)(\.mp4|p\.mp4|\?)',
            rf'\g<1>{target_q}\g<3>',
            stream_url
        )
        req = urllib.request.Request(
            candidate_url,
            headers={"Referer": referer, "Cookie": cookie_header, "User-Agent": USER_AGENT},
            method="HEAD"
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as resp:
                if resp.status == 200:
                    print(f"[+] Verified stream variant: {target_q}p", flush=True)
                    available.append(target_q)
        except Exception:
            continue

    return available if available else ["1080"]

def probe(target_url: str, output_file: str):
    """Scans and intercepts CDP stream tokens, discovers available qualities, and outputs PROBE_DATA."""
    sniffed_media_urls = set()
    session_cookies = []

    def process_url(url: str):
        if not url:
            return
        if "get_stream" in url or ((".mp4" in url or ".m3u8" in url) and "tile.vtt" not in url):
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

            click_script = """() => {
                document.querySelectorAll('video').forEach(v => {
                    try { v.muted = true; v.play(); } catch(e){}
                });
                document.querySelectorAll('.fp-player, .play-button, button, video, [class*="play"]').forEach(btn => {
                    try { btn.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window })); } catch(e){}
                });
            }"""

            for _ in range(12):
                if any("get_stream" in u for u in sniffed_media_urls):
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

    print("[*] Probing for all available stream resolutions...", flush=True)
    working_qualities = probe_available_qualities(selected_url, referer, cookie_header_val)

    payload = {
        "base_stream": selected_url,
        "referer": referer,
        "cookies": cookie_header_val,
        "filename": output_file,
        "qualities": working_qualities
    }

    print("=" * 60, flush=True)
    print(f"[✓] RESOLVED BASE TOKEN: {selected_url}", flush=True)
    print(f"[✓] AVAILABLE RESOLUTIONS: {', '.join(working_qualities)}p", flush=True)
    print("=" * 60, flush=True)

    print(f"PROBE_DATA:{json.dumps(payload)}", flush=True)
    return True