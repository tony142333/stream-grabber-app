import sys
import os
import re
from urllib.parse import urlparse

from modules.config_manager.config_core import ConfigEngine
from engines import engine_bigcdn
from engines import engine_tamperdev

if len(sys.argv) < 2:
    print("Usage: python3 get_stream.py <STREAMING_PAGE_URL>", flush=True)
    sys.exit(1)

target_url = sys.argv[1]
download_dir = os.path.expanduser("~/downloads")
os.makedirs(download_dir, exist_ok=True)

# 1. Match Site Profile from Config Core
engine_matcher = ConfigEngine()
cfg = engine_matcher.match_url(target_url)

profile_name = cfg.get("name", "Default")
engine_mode = cfg.get("engine_mode", "bigcdn").lower()

# 2. Resolve Output Filename
parsed_path = urlparse(target_url).path.strip("/")
last_segment = parsed_path.split("/")[-1] if parsed_path else "video"
base_name = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_segment, flags=re.IGNORECASE)
base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._")
if not base_name:
    base_name = "downloaded_video"

output_file = f"{base_name}.mp4"

print("=" * 65, flush=True)
print(f"[*] Matched Profile : {profile_name}", flush=True)
print(f"[*] Engine Selected : {engine_mode.upper()}", flush=True)
print(f"[*] Output Target   : {output_file}", flush=True)
print("=" * 65, flush=True)

# 3. Route Execution
if engine_mode == "tamperdev":
    success = engine_tamperdev.run(target_url, output_file, download_dir)
else:
    success = engine_bigcdn.run(target_url, output_file, download_dir)

if not success:
    sys.exit(1)