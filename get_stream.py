import sys
import os
import re
from urllib.parse import urlparse

from modules.config_manager.config_core import ConfigEngine
from engines import engine_direct_cdn
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

profile_name = cfg.get("name", "Auto / Generic Fallback")
engine_mode = cfg.get("engine_mode", "direct_cdn").lower()
title_mode = cfg.get("title_mode", "page_url")
title_pattern = cfg.get("title_pattern", "")

print("=" * 65, flush=True)
print(f"[*] Matched Profile : {profile_name}", flush=True)
print(f"[*] Engine Mode     : {engine_mode.upper()}", flush=True)
print(f"[*] Target URL      : {target_url}", flush=True)
print("=" * 65, flush=True)

# 2. Derive Filename from URL Pattern or Slug
def resolve_filename(url: str) -> str:
    if title_mode == "page_url" and title_pattern:
        match = re.search(title_pattern, url)
        if match:
            slug = match.group(1) if match.groups() else match.group(0)
            clean = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', slug).strip("._")
            if clean:
                return f"{clean}.mp4"

    parsed_path = urlparse(url).path.strip("/")
    last_seg = parsed_path.split("/")[-1] if parsed_path else "video"
    clean_seg = re.sub(r'\.(html|htm|php|asp|aspx)$', '', last_seg, flags=re.IGNORECASE)
    clean_seg = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', clean_seg).strip("._")
    return f"{clean_seg if clean_seg else 'downloaded_video'}.mp4"

output_filename = resolve_filename(target_url)

# 3. Route Execution to the Designated Engine
if engine_mode == "tamperdev":
    success = engine_tamperdev.run(target_url, cfg, output_filename, download_dir)
else:
    # Handles "direct_cdn", "standard", or fallback
    success = engine_direct_cdn.run(target_url, cfg, output_filename, download_dir)

if not success:
    sys.exit(1)