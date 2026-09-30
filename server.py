import os
import pty
import select
import asyncio
import subprocess
import re
import uuid
import sys
import shutil
import signal
from datetime import datetime
from contextlib import asynccontextmanager

# Added BackgroundTasks
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from modules.config_manager.config_core import config_router, CONFIG_DIR

# 1. Import the PB Engine
from engines.pimpbunny_engine import PimpBunnyEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_PATH = os.path.expanduser("~/downloads")

log_subscribers: set = set()   # one asyncio.Queue per open /api/logs connection
main_loop = None
PB_MAX_PARALLEL = int(os.environ.get("PB_MAX_PARALLEL", "2"))  # set by systemd on EC2; simultaneous PB probe+download jobs
pb_semaphore = None
pb_jobs: set = set()           # strong refs so background tasks aren't garbage-collected
active_tasks = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global main_loop, pb_semaphore
    main_loop = asyncio.get_running_loop()
    pb_semaphore = asyncio.Semaphore(PB_MAX_PARALLEL)
    os.makedirs(DOWNLOADS_PATH, exist_ok=True)
    yield

app = FastAPI(title="EC2 Stream Grabber Console", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.include_router(config_router, prefix="/api")

# 2. Initialize the PB Engine
pb_engine = PimpBunnyEngine(headless=True)

# ----------------- Data Models ----------------- #
class BatchRunRequest(BaseModel):
    urls: list[str]

class DownloadTriggerRequest(BaseModel):
    task_id: str
    target_stream_url: str
    output_filename: str
    referer: str
    cookies: str = ""

class CatalogRequest(BaseModel):
    creator_url: str

class BatchDownloadRequest(BaseModel):
    video_urls: list[str]
    quality: str = "1080p"

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)

def _broadcast(msg: str):
    for q in list(log_subscribers):
        q.put_nowait(msg)

def push_log_sync(msg: str):
    """Thread-safe: callable from worker threads or from the event loop."""
    if main_loop:
        main_loop.call_soon_threadsafe(_broadcast, msg)

def stream_process_worker(task_id: str, target_url: str):
    python_bin = sys.executable
    scan_script = os.path.join(BASE_DIR, "get_stream.py")

    push_log_sync(f"STATUS:{task_id}:SEARCHING:Initializing Playwright session...")
    push_log_sync(f"[{task_id}] [*] Starting task for: {target_url}")

    master_fd, slave_fd = pty.openpty()
    p = subprocess.Popen(
        [python_bin, scan_script, target_url],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=os.setsid
    )
    os.close(slave_fd)
    active_tasks[task_id] = {"process": p, "status": "SEARCHING"}

    buffer = ""
    probe_successful = False

    while True:
        r, _, _ = select.select([master_fd], [], [], 0.1)
        if master_fd in r:
            try:
                data = os.read(master_fd, 1024).decode("utf-8", errors="replace")
                if not data:
                    break
                buffer += data
                while "\r" in buffer or "\n" in buffer:
                    r_pos = buffer.find("\r")
                    n_pos = buffer.find("\n")

                    if r_pos != -1 and (n_pos == -1 or r_pos < n_pos):
                        line, buffer = buffer[:r_pos], buffer[r_pos + 1:]
                    else:
                        line, buffer = buffer[:n_pos], buffer[n_pos + 1:]

                    clean_line = strip_ansi(line).strip()
                    if clean_line:
                        push_log_sync(f"[{task_id}] {clean_line}")

                        if clean_line.startswith("PROBE_DATA:"):
                            probe_successful = True
                            push_log_sync(f"PROBE_DATA:{task_id}:{clean_line.replace('PROBE_DATA:', '', 1)}")
                            push_log_sync(f"STATUS:{task_id}:AWAITING_SELECTION:Choose quality")
                        elif "[*] Navigating to:" in clean_line or "[*] Launching" in clean_line:
                            push_log_sync(f"STATUS:{task_id}:SEARCHING:Navigating and sniffing stream tokens...")
                        elif any(k in clean_line for k in ["[+] Captured", "[*] Probing for all available", "[+] Verified stream variant"]):
                            push_log_sync(f"STATUS:{task_id}:FOUND:Stream intercepted. Testing resolutions...")
                        elif "[-] Error:" in clean_line:
                            push_log_sync(f"STATUS:{task_id}:FAILED:{clean_line}")

            except OSError:
                break

        if p.poll() is not None:
            break

    os.close(master_fd)
    p.wait()

    if p.returncode == 0:
        if not probe_successful:
            push_log_sync(f"STATUS:{task_id}:FAILED:No stream variants discovered")
    else:
        if active_tasks.get(task_id, {}).get("status") != "STOPPED":
            push_log_sync(f"STATUS:{task_id}:FAILED:Scan exited with code {p.returncode}")

def download_process_worker(task_id: str, stream_url: str, output_file: str, referer: str, cookies: str):
    python_bin = sys.executable
    downloader_script = os.path.join(BASE_DIR, "downloader.py")

    push_log_sync(f"STATUS:{task_id}:DOWNLOADING:Starting download...")
    push_log_sync(f"FILENAME:{task_id}:{output_file}")

    master_fd, slave_fd = pty.openpty()
    p = subprocess.Popen(
        [python_bin, downloader_script, stream_url, output_file, referer, cookies],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
        preexec_fn=os.setsid
    )
    os.close(slave_fd)
    active_tasks[task_id] = {"process": p, "status": "DOWNLOADING", "output_file": output_file}

    buffer = ""
    while True:
        r, _, _ = select.select([master_fd], [], [], 0.1)
        if master_fd in r:
            try:
                data = os.read(master_fd, 1024).decode("utf-8", errors="replace")
                if not data:
                    break
                buffer += data
                while "\r" in buffer or "\n" in buffer:
                    r_pos = buffer.find("\r")
                    n_pos = buffer.find("\n")
                    if r_pos != -1 and (n_pos == -1 or r_pos < n_pos):
                        line, buffer = buffer[:r_pos], buffer[r_pos + 1:]
                    else:
                        line, buffer = buffer[:n_pos], buffer[n_pos + 1:]

                    clean_line = strip_ansi(line).strip()
                    if clean_line:
                        push_log_sync(f"[{task_id}] {clean_line}")
                        if clean_line.startswith("[#") and ("DL:" in clean_line or "%" in clean_line):
                            push_log_sync(f"PROGRESS:{task_id}:{clean_line}")
            except OSError:
                break

        if p.poll() is not None:
            break

    os.close(master_fd)
    p.wait()

    current_state = active_tasks.get(task_id, {}).get("status")
    if current_state == "STOPPED":
        push_log_sync(f"STATUS:{task_id}:STOPPED:Download stopped")
        push_log_sync(f"[{task_id}] [!] Download stopped by user")
    elif p.returncode == 0:
        push_log_sync(f"STATUS:{task_id}:COMPLETED:Finished")
        push_log_sync(f"[{task_id}] [✓] Task completed successfully")
    else:
        push_log_sync(f"STATUS:{task_id}:FAILED:Download exited with code {p.returncode}")
        push_log_sync(f"[{task_id}] [✗] Download failed with code {p.returncode}")

    active_tasks.pop(task_id, None)

# ----------------- UI Routes ----------------- #
@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))

@app.get("/pb")
def pb_dashboard():
    # Matches your existing FileResponse pattern instead of Jinja2Templates
    return FileResponse(os.path.join(BASE_DIR, "templates", "pb.html"))

@app.get("/api/config-panel-template")
def get_config_panel_template():
    return FileResponse(os.path.join(CONFIG_DIR, "config_panel.html"))

# ----------------- System Endpoints ----------------- #
@app.get("/api/sysinfo")
def get_sys_info():
    total, used, free = shutil.disk_usage(DOWNLOADS_PATH)
    return {
        "disk_free_gb": round(free / (1024 ** 3), 2),
        "disk_total_gb": round(total / (1024 ** 3), 2),
        "disk_used_percent": round((used / total) * 100, 1),
        "download_dir": DOWNLOADS_PATH
    }

@app.get("/api/files")
def list_completed_files():
    files_list = []
    if os.path.exists(DOWNLOADS_PATH):
        for entry in os.scandir(DOWNLOADS_PATH):
            if entry.is_file() and not entry.name.endswith(".aria2"):
                if os.path.exists(f"{entry.path}.aria2"):
                    continue

                stat = entry.stat()
                files_list.append({
                    "name": entry.name,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                })
    files_list.sort(key=lambda x: x["modified"], reverse=True)
    return {"files": files_list}

@app.post("/api/run-batch")
async def run_batch(req: BatchRunRequest):
    loop = asyncio.get_running_loop()
    created_tasks = []
    for raw_url in req.urls:
        t_id = uuid.uuid4().hex[:6]
        created_tasks.append({"id": t_id, "url": raw_url})
        loop.run_in_executor(None, stream_process_worker, t_id, raw_url)
    return {"status": "queued", "tasks": created_tasks}

@app.post("/api/start-download")
async def start_download_endpoint(req: DownloadTriggerRequest):
    loop = asyncio.get_running_loop()
    loop.run_in_executor(
        None, download_process_worker,
        req.task_id, req.target_stream_url, req.output_filename, req.referer, req.cookies
    )
    return {"status": "started", "task_id": req.task_id}

@app.post("/api/task/{task_id}/pause")
def pause_task(task_id: str):
    info = active_tasks.get(task_id)
    if not info or not info.get("process"):
        raise HTTPException(status_code=404, detail="Task not actively running")
    try:
        os.killpg(os.getpgid(info["process"].pid), signal.SIGSTOP)
        info["status"] = "PAUSED"
        push_log_sync(f"STATUS:{task_id}:PAUSED:Paused")
        push_log_sync(f"[{task_id}] [⏸] Download paused")
        return {"status": "paused"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/task/{task_id}/resume")
def resume_task(task_id: str):
    info = active_tasks.get(task_id)
    if not info or not info.get("process"):
        raise HTTPException(status_code=404, detail="Task not actively paused")
    try:
        os.killpg(os.getpgid(info["process"].pid), signal.SIGCONT)
        info["status"] = "DOWNLOADING"
        push_log_sync(f"STATUS:{task_id}:DOWNLOADING:Resumed")
        push_log_sync(f"[{task_id}] [▶] Download resumed")
        return {"status": "resumed"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/task/{task_id}/stop")
def stop_task(task_id: str):
    info = active_tasks.get(task_id)
    if not info or not info.get("process"):
        raise HTTPException(status_code=404, detail="Task not actively running")
    try:
        info["status"] = "STOPPED"
        os.killpg(os.getpgid(info["process"].pid), signal.SIGTERM)
        return {"status": "stopping"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

def range_stream_file(file_path: str, range_header: str | None):
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(file_path)
    start = 0
    end = file_size - 1

    if range_header:
        range_match = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if range_match:
            start = int(range_match.group(1))
            if range_match.group(2):
                end = int(range_match.group(2))

    start = max(0, min(start, file_size - 1))
    end = max(start, min(end, file_size - 1))
    content_length = (end - start) + 1

    def iter_file():
        with open(file_path, "rb") as f:
            f.seek(start)
            bytes_left = content_length
            chunk_size = 1024 * 512
            while bytes_left > 0:
                read_amount = min(chunk_size, bytes_left)
                data = f.read(read_amount)
                if not data:
                    break
                bytes_left -= len(data)
                yield data

    headers = {
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(content_length),
        "Content-Type": "video/mp4",
    }
    return StreamingResponse(iter_file(), status_code=206 if range_header else 200, headers=headers)

@app.get("/api/preview/active/{task_id}")
async def preview_active_stream(task_id: str, request: Request):
    info = active_tasks.get(task_id)
    target_name = info.get("output_file") if info else None

    target_path = None
    if target_name:
        candidate = os.path.join(DOWNLOADS_PATH, target_name)
        if os.path.exists(candidate):
            target_path = candidate

    if not target_path:
        for entry in os.scandir(DOWNLOADS_PATH):
            if entry.is_file() and not entry.name.endswith(".aria2"):
                if os.path.exists(f"{entry.path}.aria2"):
                    target_path = entry.path
                    break

    if not target_path or os.path.getsize(target_path) < 1024 * 128:
        raise HTTPException(status_code=425, detail="Buffering initial chunks. Wait a few seconds.")

    return range_stream_file(target_path, request.headers.get("range"))

@app.get("/api/preview/file/{filename}")
async def preview_completed_stream(filename: str, request: Request):
    safe_name = os.path.basename(filename)
    target_path = os.path.join(DOWNLOADS_PATH, safe_name)
    return range_stream_file(target_path, request.headers.get("range"))

@app.get("/api/logs")
async def stream_logs():
    async def event_generator():
        q = asyncio.Queue()
        log_subscribers.add(q)
        try:
            while True:
                msg = await q.get()
                yield f"data: {msg}\n\n"
        finally:
            log_subscribers.discard(q)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

# ----------------- PB Bulk Scraper Endpoints ----------------- #
def pick_quality(requested: str, available: list[str]):
    """Exact match, else best quality below the request, else lowest above it."""
    if requested in available:
        return requested
    want = int(requested.rstrip("p") or 0)
    nums = sorted(int(q.rstrip("p")) for q in available)
    below = [n for n in nums if n <= want]
    chosen = below[-1] if below else (nums[0] if nums else None)
    return f"{chosen}p" if chosen else None

def make_filename(url: str, quality: str, task_id: str) -> str:
    slug = url.rstrip("/").split("/")[-1] or f"pb_{task_id}"
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", slug).strip("_")[:120]
    name = f"{slug}_{quality}.mp4"
    if os.path.exists(os.path.join(DOWNLOADS_PATH, name)):
        name = f"{slug}_{quality}_{task_id}.mp4"
    return name

@app.post("/api/pb/catalog")
async def get_catalog(payload: CatalogRequest):
    """Crawl paginated creator pages and return [{url,title,thumb}, ...]."""
    try:
        videos = await pb_engine.get_creator_videos(payload.creator_url)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Scrape failed: {e}")
    return {"status": "success", "count": len(videos), "videos": videos}

async def process_one_video(task_id: str, url: str, quality: str):
    """Probe one video, then run the existing download worker. Limited by pb_semaphore."""
    loop = asyncio.get_running_loop()
    async with pb_semaphore:
        try:
            push_log_sync(f"STATUS:{task_id}:SEARCHING:Probing video page...")
            push_log_sync(f"[{task_id}] [PB] Probing: {url}")
            probe = await pb_engine.probe_video(url)

            if "error" in probe:
                push_log_sync(f"STATUS:{task_id}:FAILED:{probe['error']}")
                return

            chosen = pick_quality(quality, probe["available_qualities"])
            if not chosen:
                push_log_sync(f"STATUS:{task_id}:FAILED:No qualities found")
                return
            if chosen != quality:
                push_log_sync(f"[{task_id}] [PB] {quality} unavailable, using {chosen}")

            filename = make_filename(url, chosen, task_id)
            push_log_sync(f"STATUS:{task_id}:FOUND:Stream found at {chosen}")
            # download_process_worker emits FILENAME/STATUS/PROGRESS with this same task_id.
            # Awaiting it keeps the semaphore held until the file is finished.
            await loop.run_in_executor(
                None, download_process_worker,
                task_id, probe["streams"][chosen], filename,
                probe.get("referer", url), probe.get("cookies", "")
            )
        except Exception as e:
            push_log_sync(f"STATUS:{task_id}:FAILED:{type(e).__name__}: {e}")

@app.post("/api/pb/queue")
async def queue_batch(payload: BatchDownloadRequest):
    """Create task IDs up front (so the UI can track them), then process in the background."""
    if not payload.video_urls:
        raise HTTPException(status_code=400, detail="No videos selected.")

    tasks = []
    for url in payload.video_urls:
        t_id = uuid.uuid4().hex[:6]
        tasks.append({"id": t_id, "url": url})
        push_log_sync(f"STATUS:{t_id}:QUEUED:Waiting for a free slot")
        job = asyncio.create_task(process_one_video(t_id, url, payload.quality))
        pb_jobs.add(job)
        job.add_done_callback(pb_jobs.discard)

    return {"status": "queued", "quality": payload.quality, "tasks": tasks}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8085)