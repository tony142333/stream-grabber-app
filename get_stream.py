import sys
import os
import re
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from modules.config_manager.config_core import ConfigEngine
from engines import engine_bigcdn
from engines import engine_tamperdev

if len(sys.argv) < 2:
    print("Usage: python3 get_stream.py <STREAMING_PAGE_URL>", flush=True)
    sys.exit(1)

target_url = sys.argv[1]

# 1. Match Site Profile from Config Core
engine_matcher = ConfigEngine()
cfg = engine_matcher.match_url(target_url)

profile_name = cfg.get("name", "Default")
engine_mode = cfg.get("engine_mode", "bigcdn").lower()

# 2. Resolve Robust Output Filename
segments = [s for s in urlparse(target_url).path.strip("/").split("/") if s]
target_segment = "downloaded_video"

for s in reversed(segments):
    cleaned = re.sub(r'\.(html|htm|php|asp|aspx)$', '', s, flags=re.IGNORECASE)
    cleaned = re.sub(r'[^a-zA-Z0-9_\-]', '_', cleaned).strip("._")
    if len(cleaned) > 2 and cleaned.lower() not in ["video", "watch", "play", "view", "v"]:
        target_segment = cleaned
        break
else:
    if segments:
        target_segment = re.sub(r'[^a-zA-Z0-9_\-]', '_', segments[-1]).strip("._") or "downloaded_video"

output_file = f"{target_segment}.mp4"

print("=" * 65, flush=True)
print(f"[*] Matched Profile : {profile_name}", flush=True)
print(f"[*] Engine Selected : {engine_mode.upper()}", flush=True)
print(f"[*] Target Filename : {output_file}", flush=True)
print("=" * 65, flush=True)

# 3. Route Execution to PROBE
if engine_mode == "tamperdev":
    success = engine_tamperdev.probe(target_url, output_file)
else:
    success = engine_bigcdn.probe(target_url, output_file)

if not success:
    sys.exit(1)

sys.exit(0)