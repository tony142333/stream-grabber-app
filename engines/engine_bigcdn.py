import os
import re
import subprocess
import urllib.request
from urllib.parse import urlparse
from typing import Optional
from playwright.sync_api import sync_playwright

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
DEFAULT_QUALITIES = ["2160", "4k", "1440", "1080", "720", "480", "360"]

def probe_quality(base_url: str, referer: str, quality_hierarchy: list) -> str:
    """Probes HEAD request for target quality and falls back down the hierarchy."""
    for q in quality_hierarchy:
        test_url = f"{base_url}/{q}.mp4"
        req = urllib.request.Request(
            test_url,
            headers={
                "Referer": referer,
                "User-Agent": USER_AGENT
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
    fallback_q = quality_hierarchy[0] if quality_hierarchy else "1080"
    return f"{base_url}/{fallback_q}.mp4"

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
    sniff_keywords = cfg.get("sniff_keywords", ["bigcdn.cc", ".mp4"])
    click_selectors = cfg.get("click_selectors", [".fluid_initial_play_button", ".play", "button", "#player"])

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
            user_agent=USER_AGENT
        )

        def process_url(url: str):
            if not url:
                return

            if any(kw.lower() in url.lower() for kw in sniff_keywords):
                if any(f"/{q}.mp4" in url or f"_{q}p" in url for q in quality_hierarchy) or url.endswith(".mp4"):
                    sniffed_mp4_urls.add(url)
                    print(f"[+] Sniffed Stream URL: {url}", flush=True)
                elif "tile.vtt" in url or "main.jpg" in url or "preview.mp4" in url:
                    base = url.rsplit("/", 1)[0]
                    base_cdn_paths.add(base)

        page.on("request", lambda req: process_url(req.url))
        page.on("response", lambda res: process_url(res.url))

        try:
            page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

            selector_array_js = str(click_selectors)
            interaction_script = f"""() => {{
                document.querySelectorAll('video').forEach(v => {{ v.muted = true; try {{ v.play(); }} catch(e){{}} }});
                const selectors = {selector_array_js};
                selectors.forEach(sel => {{
                    document.querySelectorAll(sel).forEach(b => {{ try {{ b.click(); }} catch(e){{}} }});
                }});
            }}"""

            for _ in range(12):
                if sniffed_mp4_urls or base_cdn_paths:
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

    target_stream_url = None
    primary_cdn_base = None

    if sniffed_mp4_urls:
        for q in quality_hierarchy:
            matched = [u for u in sniffed_mp4_urls if f"/{q}.mp4" in u or f"_{q}p" in u]
            if matched:
                target_stream_url = matched[0]
                primary_cdn_base = target_stream_url.rsplit("/", 1)[0]
                break
        if not primary_cdn_base and sniffed_mp4_urls:
            primary_cdn_base = list(sniffed_mp4_urls)[0].rsplit("/", 1)[0]
    elif base_cdn_paths:
        primary_cdn_base = list(base_cdn_paths)[0]

    if not primary_cdn_base and not target_stream_url:
        print("[-] Error: No matching stream or CDN path found.", flush=True)
        return False

    referer_match = re.search(r'(https?://[^/]+)/?', primary_cdn_base if primary_cdn_base else target_stream_url)
    referer = referer_match.group(0) if referer_match else "https://s6.bigcdn.cc/"

    if primary_cdn_base:
        print(f"[*] Probing CDN path with quality hierarchy: {' -> '.join(quality_hierarchy)}...", flush=True)
        target_stream_url = probe_quality(primary_cdn_base, referer, quality_hierarchy)

    print("=" * 60, flush=True)
    print(f"SELECTED STREAM URL : {target_stream_url}", flush=True)
    print(f"SAVING TO           : {download_dir}/{output_file}", flush=True)
    print("=" * 60, flush=True)

    aria2_cmd = [
        "aria2c",
        "-x", "16",
        "-s", "16",
        "-k", "1M",
        "--summary-interval=1",
        f"--header=Referer: {referer}",
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