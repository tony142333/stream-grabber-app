import sys
import re
import os
import subprocess
from urllib.parse import urlparse
import urllib.request
from playwright.sync_api import sync_playwright

if len(sys.argv) < 2:
    print("Usage: python3 get_stream.py <STREAMING_PAGE_URL>", flush=True)
    sys.exit(1)

target_url = sys.argv[1]

# Extract clean filename slug
parsed_path = urlparse(target_url).path.strip("/")
last_segment = parsed_path.split("/")[-1] if parsed_path else "video"
base_name = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_segment, flags=re.IGNORECASE)
base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._")
if not base_name:
    base_name = "downloaded_video"

download_dir = os.path.expanduser("~/downloads")
os.makedirs(download_dir, exist_ok=True)
output_file = f"{base_name}.mp4"

# Quality hierarchy
QUALITY_VARIANTS = ["2160", "4k", "1440", "1080", "720", "480", "360"]

sniffed_bigcdn_urls = set()
cdn_base_paths = set()

def probe_highest_quality(base_url: str, referer: str) -> str:
    """Probes HEAD request for highest available quality on the BigCDN path."""
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
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                if resp.status == 200:
                    print(f"[+] Verified available stream: {q}p -> {test_url}", flush=True)
                    return test_url
        except Exception:
            continue
    return None

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

        # Capture direct BigCDN MP4 streams
        if re.search(r'/pubs/[^/]+/(2160|4k|1440|1080|720|480|360|\w+)\.mp4', url):
            if "preview" not in url:
                sniffed_bigcdn_urls.add(url)
                print(f"[+] Sniffed BigCDN Stream URL: {url}", flush=True)
        elif "/pubs/" in url and ("tile.vtt" in url or "main.jpg" in url or "preview.mp4" in url):
            base = url.rsplit("/", 1)[0]
            cdn_base_paths.add(base)

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)

        for _ in range(12):
            if sniffed_bigcdn_urls or cdn_base_paths:
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

target_stream_url = None
primary_cdn_base = None

if sniffed_bigcdn_urls:
    target_stream_url = list(sniffed_bigcdn_urls)[0]
    primary_cdn_base = target_stream_url.rsplit("/", 1)[0]
elif cdn_base_paths:
    primary_cdn_base = list(cdn_base_paths)[0]

if not primary_cdn_base and not target_stream_url:
    print("[-] Error: No matching BigCDN stream found.", flush=True)
    sys.exit(1)

referer_match = re.search(r'(https?://[^/]+bigcdn\.cc)/', primary_cdn_base if primary_cdn_base else target_stream_url)
referer = f"{referer_match.group(1)}/" if referer_match else "https://s29.bigcdn.cc/"

if primary_cdn_base:
    print(f"[*] Checking CDN path ({primary_cdn_base}) for highest available resolution...", flush=True)
    probed_url = probe_highest_quality(primary_cdn_base, referer)
    if probed_url:
        target_stream_url = probed_url
    elif not target_stream_url:
        target_stream_url = f"{primary_cdn_base}/1080.mp4"

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
sys.exit(proc.returncode)