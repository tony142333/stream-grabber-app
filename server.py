import os
import pty
import select
import asyncio
import subprocess
import re
import uuid
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

log_queue = None
main_loop = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global log_queue, main_loop
    log_queue = asyncio.Queue()
    main_loop = asyncio.get_running_loop()
    yield

app = FastAPI(title="EC2 Stream Grabber Console", lifespan=lifespan)

class BatchRunRequest(BaseModel):
    urls: list[str]

ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub('', text)

def push_log_sync(msg: str):
    if main_loop and log_queue:
        asyncio.run_coroutine_threadsafe(log_queue.put(msg), main_loop)

def stream_process_worker(task_id: str, target_url: str):
    script_path = "/home/ubuntu/get_stream.py"
    python_bin = sys.executable  # Uses current active venv python

    push_log_sync(f"[{task_id}] [*] Starting task for: {target_url}")

    master_fd, slave_fd = pty.openpty()
    p = subprocess.Popen(
        [python_bin, script_path, target_url],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True
    )
    os.close(slave_fd)

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
            except OSError:
                break

        if p.poll() is not None:
            try:
                trailing = strip_ansi(os.read(master_fd, 1024).decode("utf-8", errors="replace")).strip()
                if trailing:
                    push_log_sync(f"[{task_id}] {trailing}")
            except OSError:
                pass
            break

    os.close(master_fd)
    p.wait()
    push_log_sync(f"[{task_id}] [✓] Task completed with exit code {p.returncode}")

@app.get("/", response_class=HTMLResponse)
def index():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>EC2 Stream Grabber Console</title>
      <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Consolas', 'Courier New', monospace; }
        body { background: #0c1017; color: #38bdf8; display: flex; justify-content: center; min-height: 100vh; padding: 1.5rem; }
        .container { width: 100%; max-width: 1050px; display: flex; flex-direction: column; gap: 1rem; }
        .input-card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 1rem; display: flex; flex-direction: column; gap: 0.75rem; }
        textarea { width: 100%; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; color: #fff; font-size: 0.9rem; font-family: inherit; resize: vertical; min-height: 75px; }
        textarea:focus { outline: 1px solid #38bdf8; }
        .btn-row { display: flex; justify-content: space-between; align-items: center; }
        button { background: #238636; color: white; border: none; border-radius: 6px; padding: 10px 22px; font-size: 0.9rem; font-weight: bold; cursor: pointer; }
        button:hover { background: #2ea043; }
        .hint { color: #8b949e; font-size: 0.8rem; }
        .terminal { background: #010409; border: 1px solid #30363d; border-radius: 8px; padding: 1.2rem; height: 560px; overflow-y: auto; color: #e6edf3; font-size: 0.85rem; line-height: 1.45; display: flex; flex-direction: column; }
        .log-entry { margin-bottom: 3px; word-break: break-all; white-space: pre-wrap; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="input-card">
          <textarea id="targetUrls" placeholder="Paste single or multiple streaming URLs (one per line)..."></textarea>
          <div class="btn-row">
            <span class="hint">Native Host Execution | Multi-stream ready</span>
            <button onclick="startBatch()">Run Task(s)</button>
          </div>
        </div>

        <div class="terminal" id="term">
          <div class="log-entry" style="color: #8b949e;">[+] Host Console connected on port 8085. Ready.</div>
        </div>
      </div>

      <script>
        const term = document.getElementById('term');
        const progressLines = {};

        function appendLog(rawText) {
          const match = rawText.match(/^\\[([a-zA-Z0-9_-]+)\\]\\s*(.*)$/);
          const taskId = match ? match[1] : 'sys';
          const content = match ? match[2] : rawText;

          const isProgress = content.includes('[#') && (content.includes('CN:') || content.includes('DL:') || content.includes('ETA:'));

          if (isProgress) {
            if (progressLines[taskId] && progressLines[taskId].parentNode) {
              progressLines[taskId].textContent = `[${taskId}] ` + content;
              progressLines[taskId].style.color = '#58a6ff';
            } else {
              const div = document.createElement('div');
              div.className = 'log-entry';
              div.style.color = '#58a6ff';
              div.textContent = `[${taskId}] ` + content;
              term.appendChild(div);
              progressLines[taskId] = div;
            }
          } else {
            delete progressLines[taskId];
            const div = document.createElement('div');
            div.className = 'log-entry';
            if (content.includes('[+]') || content.includes('TARGET STREAM')) div.style.color = '#38bdf8';
            else if (content.includes('[✓]')) div.style.color = '#3fb950';
            else if (content.includes('[-]')) div.style.color = '#f85149';
            div.textContent = rawText;
            term.appendChild(div);
          }
          term.scrollTop = term.scrollHeight;
        }

        const evtSource = new EventSource("/api/logs");
        evtSource.onmessage = function(event) {
          appendLog(event.data);
        };

        async function startBatch() {
          const input = document.getElementById('targetUrls');
          const raw = input.value.trim();
          if (!raw) return;

          const urls = raw.split(/\\n+/).map(u => u.trim()).filter(u => u.length > 0);
          if (urls.length === 0) return;

          input.value = '';

          await fetch('/api/run-batch', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ urls: urls })
          });
        }
      </script>
    </body>
    </html>
    """

@app.post("/api/run-batch")
async def run_batch(req: BatchRunRequest):
    loop = asyncio.get_running_loop()
    for raw_url in req.urls:
        t_id = uuid.uuid4().hex[:6]
        loop.run_in_executor(None, stream_process_worker, t_id, raw_url)
    return {"status": "queued", "count": len(req.urls)}

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
