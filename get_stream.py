import sys
import os
import re
from urllib.parse import urlparse

from modules.config_manager.config_core import ConfigEngine
from engines import engine_bigcdn
from engines import engine_tamperdev

if len(sys.argv) < 2:
    print("Usage: python3 get_stream.py <STREAMING_PAGE_URL> [QUALITY]", flush=True)
    print("Example: python3 get_stream.py https://example.com/video 720", flush=True)
    sys.exit(1)

target_url = sys.argv[1]
# Accept optional target quality argument from CLI (e.g., '1080', '720', '480', 'best')
requested_quality = sys.argv[2].lower() if len(sys.argv) > 2 else "best"

download_dir = os.path.expanduser("~/downloads")
os.makedirs(download_dir, exist_ok=True)

# 1. Match Site Profile from Config Core
engine_matcher = ConfigEngine()
cfg = engine_matcher.match_url(target_url)

profile_name = cfg.get("name", "Default")
engine_mode = cfg.get("engine_mode", "bigcdn").lower()

# Build prioritized quality preference list based on requested quality
STANDARD_QUALITIES = ["2160", "4k", "1440", "1080", "720", "480", "360"]
config_qualities = cfg.get("quality_preference", STANDARD_QUALITIES)

if requested_quality not in ["best", "auto", "max"]:
    # Clean string (e.g., '720p' -> '720')
    cleaned_q = requested_quality.replace("p", "")
    if cleaned_q in config_qualities:
        # Move target quality to index 0, followed by remaining lower qualities as fallback
        idx = config_qualities.index(cleaned_q)
        active_quality_preference = [cleaned_q] + config_qualities[idx + 1:] + config_qualities[:idx]
    else:
        active_quality_preference = [cleaned_q] + config_qualities
else:
    active_quality_preference = config_qualities

# Pass the updated preference order into the config object
cfg["quality_preference"] = active_quality_preference

# 2. Resolve Output Filename
title_mode = cfg.get("title_mode", "page_url")
title_pattern = cfg.get("title_pattern", "")

base_name = None
if title_mode == "page_url" and title_pattern:
    match = re.search(title_pattern, target_url)
    if match:
        base_name = match.group(1) if match.groups() else match.group(0)
        base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._")

if not base_name:
    parsed_path = urlparse(target_url).path.strip("/")
    last_segment = parsed_path.split("/")[-1] if parsed_path else "video"
    base_name = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_segment, flags=re.IGNORECASE)
    base_name = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', base_name).strip("._")
    if not base_name:
        base_name = "downloaded_video"

output_file = f"{base_name}.mp4"

print("=" * 65, flush=True)
print(f"[*] Matched Profile  : {profile_name}", flush=True)
print(f"[*] Engine Selected  : {engine_mode.upper()}", flush=True)
print(f"[*] Target Quality   : {requested_quality.upper()}", flush=True)
print(f"[*] Quality Priority : {' -> '.join(active_quality_preference)}", flush=True)
print(f"[*] Output Target    : {output_file}", flush=True)
print("=" * 65, flush=True)

# 3. Route Execution
if engine_mode == "tamperdev":
    success = engine_tamperdev.run(target_url, cfg, output_file, download_dir)
else:
    success = engine_bigcdn.run(target_url, cfg, output_file, download_dir)

if not success:
    sys.exit(1)