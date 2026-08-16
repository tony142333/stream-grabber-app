import sys
import re
import os
import subprocess
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

if len(sys.argv) < 2:
    print("Usage: python3 get_stream.py <STREAMING_PAGE_URL>", flush=True)
    sys.exit(1)

target_url = sys.argv[1]

# Extract filename slug
parsed_path = urlparse(target_url).path.strip("/")
last_segment = parsed_path.split("/")[-1] if parsed_path else "video"
base_name = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_segment, flags=re.IGNORECASE)
base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._")
if not base_name:
    base_name = "downloaded_video"

# Set default download destination to ~/downloads
download_dir = os.path.expanduser("~/downloads")
os.makedirs(download_dir, exist_ok=True)
output_file = f"{base_name}.mp4"

bigcdn_stream_url = None

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
        global bigcdn_stream_url
        if not url:
            return
        if "bigcdn.cc" in url:
            if re.search(r'/(1080|720|480)\.mp4', url):
                bigcdn_stream_url = url
                print(f"[+] Sniffed direct BigCDN MP4: {url}", flush=True)
            elif ("tile.vtt" in url or "main.jpg" in url) and not bigcdn_stream_url:
                base = url.rsplit("/", 1)[0]
                bigcdn_stream_url = f"{base}/1080.mp4"
                print(f"[+] Reconstructed BigCDN 1080p URL: {bigcdn_stream_url}", flush=True)

    page.on("request", lambda req: process_url(req.url))
    page.on("response", lambda res: process_url(res.url))

    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=60000)

        for _ in range(12):
            if bigcdn_stream_url:
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

if not bigcdn_stream_url:
    print("[-] Error: No matching BigCDN stream URL found.", flush=True)
    sys.exit(1)

print("=" * 60, flush=True)
print(f"TARGET STREAM DETECTED: {bigcdn_stream_url}", flush=True)
print(f"SAVING TO: {download_dir}/{output_file}", flush=True)
print("=" * 60, flush=True)

referer_match = re.search(r'(https?://[^/]+bigcdn\.cc)/', bigcdn_stream_url)
referer = f"{referer_match.group(1)}/" if referer_match else "https://s6.bigcdn.cc/"

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
    bigcdn_stream_url
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