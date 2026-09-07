import sys
import os
import subprocess

if len(sys.argv) < 4:
    print("Usage: python3 downloader.py <STREAM_URL> <OUTPUT_FILE> <REFERER> [COOKIE_HEADER]", flush=True)
    sys.exit(1)

stream_url = sys.argv[1]
output_file = sys.argv[2]
referer = sys.argv[3]
cookie_header = sys.argv[4] if len(sys.argv) > 4 else ""
download_dir = os.path.expanduser("~/downloads")

os.makedirs(download_dir, exist_ok=True)

print("=" * 60, flush=True)
print(f"[✓] STARTING DOWNLOAD: {output_file}", flush=True)
print(f"[✓] TARGET STREAM    : {stream_url}", flush=True)
print(f"[✓] SAVING TO        : {download_dir}/{output_file}", flush=True)
print("=" * 60, flush=True)

aria2_cmd = [
    "aria2c",
    "-c",
    "-x", "16",
    "-s", "16",
    "-k", "1M",
    "--summary-interval=1",
    f"--header=Referer: {referer}",
    "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    f"--dir={download_dir}",
    "-o", output_file,
]

if cookie_header.strip():
    aria2_cmd.append(f"--header=Cookie: {cookie_header}")

aria2_cmd.append(stream_url)

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

if proc.returncode != 0:
    print(f"[-] Error: aria2c exited with return code {proc.returncode}", flush=True)
    sys.exit(proc.returncode)

sys.exit(0)