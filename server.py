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
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

from modules.config_manager.config_core import config_router, CONFIG_DIR

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOWNLOADS_PATH = os.path.expanduser("~/downloads")

log_queue = None
main_loop = None
active_tasks = {}

@asynccontextmanager
async def lifespan(app: FastAPI):
    global log_queue, main_loop
    log_queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()
    os.makedirs(DOWNLOADS_PATH, exist_ok=True)
    yield

app = FastAPI(title="EC2 Stream Grabber Console", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
app.include_router(config_router, prefix="/api")

class BatchRunRequest(BaseModel):
    urls: list[str]

class DownloadTriggerRequest(BaseModel):
    task_id: str
    target_stream_url: str
    output_filename: str
    referer: str
    cookies: str = ""

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)

def push_log_sync(msg: str):
    if main_loop and log_queue:
        asyncio.run_coroutine_threadsafe(log_queue.put(msg), main_loop)

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
    active_tasks[task_id] = {"process": p, "status": "DOWNLOADING"}

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

@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "templates", "index.html"))

@app.get("/api/config-panel-template")
def get_config_panel_template():
    return FileResponse(os.path.join(CONFIG_DIR, "config_panel.html"))

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

@app.get("/api/logs")
async def stream_logs():
    async def event_generator():
        while True:
            if log_queue:
                msg = await log_queue.get()
                yield f"data: {msg}\n\n"
            else:
                await asyncio.sleep(0.1)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8085)