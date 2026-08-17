const tasks = {};
const terminalLogs = document.getElementById('terminalLogs');

function switchView(viewName, el) {
  document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');

  document.getElementById(`panel-${viewName}`).classList.add('active');
  const titles = {
    'active': 'Active Downloads',
    'completed': 'Downloaded Files (~/downloads)',
    'terminal': 'Live Mirroring Terminal'
  };
  document.getElementById('viewTitle').textContent = titles[viewName];

  if (viewName === 'completed') loadCompletedFiles();
}

function toggleInputCard() {
  const card = document.getElementById('urlInputCard');
  card.style.display = card.style.display === 'none' ? 'flex' : 'none';
  if (card.style.display === 'flex') document.getElementById('targetUrls').focus();
}

async function updateSysInfo() {
  try {
    const res = await fetch('/api/sysinfo');
    const data = await res.json();
    document.getElementById('diskFreeText').textContent = `${data.disk_free_gb} GB Free`;
    document.getElementById('diskFill').style.width = `${data.disk_used_percent}%`;
  } catch(e) {}
}
updateSysInfo();
setInterval(updateSysInfo, 10000);

async function loadCompletedFiles() {
  try {
    const res = await fetch('/api/files');
    const data = await res.json();
    const tbody = document.getElementById('filesTableBody');
    document.getElementById('completedBadge').textContent = data.files.length;

    if (data.files.length === 0) {
      tbody.innerHTML = '<tr><td colspan="4" style="text-align: center; color: var(--text-muted); padding: 2rem;">No downloaded files in ~/downloads yet.</td></tr>';
      return;
    }

    tbody.innerHTML = data.files.map(f => `
      <tr>
        <td style="color: #fff; font-weight: 500;">${f.name}</td>
        <td style="color: var(--accent);">${f.size_mb} MB</td>
        <td style="color: var(--text-muted);">${f.modified}</td>
        <td style="color: #10b981;">~/downloads</td>
      </tr>
    `).join('');
  } catch(e) {}
}
loadCompletedFiles();

function getOrCreateTask(taskId, initialName = "Sniffing stream from source...") {
  if (tasks[taskId]) return tasks[taskId];
  const noMsg = document.getElementById('noActiveMsg');
  if (noMsg) noMsg.style.display = 'none';

  const card = document.createElement('div');
  card.className = 'task-card';
  card.id = `task-${taskId}`;
  card.innerHTML = `
    <div class="task-header">
      <div class="task-name" id="name-${taskId}">${initialName}</div>
      <span class="badge badge-sniffing" id="badge-${taskId}">Searching</span>
    </div>
    <div class="progress-bar-track">
      <div class="progress-bar-fill" id="fill-${taskId}"></div>
    </div>
    <div class="task-metrics">
      <span id="speed-${taskId}">Initializing browser session...</span>
      <span id="pct-${taskId}">0%</span>
    </div>
  `;

  document.getElementById('activeList').prepend(card);
  tasks[taskId] = {
    element: card,
    name: card.querySelector(`#name-${taskId}`),
    badge: card.querySelector(`#badge-${taskId}`),
    fill: card.querySelector(`#fill-${taskId}`),
    speed: card.querySelector(`#speed-${taskId}`),
    pct: card.querySelector(`#pct-${taskId}`),
    completed: false
  };
  updateActiveCount();
  return tasks[taskId];
}

function updateActiveCount() {
  const active = Object.values(tasks).filter(t => !t.completed).length;
  document.getElementById('activeBadge').textContent = active;
  const noMsg = document.getElementById('noActiveMsg');
  if (noMsg) {
    noMsg.style.display = active === 0 ? 'block' : 'none';
  }
}

function parseAria(str) {
  const pctMatch = str.match(/\(([0-9]+)%\)/);
  const dlMatch = str.match(/DL:([^\s]+)/);
  const etaMatch = str.match(/ETA:([^\s\]]+)/);
  const cnMatch = str.match(/CN:([0-9]+)/);
  return {
    percent: pctMatch ? parseInt(pctMatch[1]) : null,
    speed: dlMatch ? dlMatch[1] : null,
    eta: etaMatch ? etaMatch[1] : null,
    cn: cnMatch ? cnMatch[1] : null
  };
}

const evtSource = new EventSource("/api/logs");
evtSource.onmessage = function(event) {
  const raw = event.data;

  const div = document.createElement('div');
  div.className = 'log-line';
  div.textContent = raw;
  terminalLogs.appendChild(div);
  terminalLogs.scrollTop = terminalLogs.scrollHeight;

  if (raw.startsWith("STATUS:")) {
    const parts = raw.split(":");
    const taskId = parts[1];
    const status = parts[2];
    const msg = parts.slice(3).join(":");
    const task = getOrCreateTask(taskId);

    if (status === "SEARCHING") {
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Searching";
      task.speed.textContent = msg || "Navigating to target page...";
    } else if (status === "FOUND") {
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Found";
      task.speed.textContent = msg || "Probing stream resolution...";
    } else if (status === "DOWNLOADING") {
      task.badge.className = "badge badge-downloading";
      task.badge.textContent = "Downloading";
      task.speed.textContent = msg || "Downloading stream...";
    } else if (status === "COMPLETED") {
      task.badge.className = "badge badge-completed";
      task.badge.textContent = "Finished";
      task.fill.style.width = "100%";
      task.pct.textContent = "100%";
      task.speed.textContent = "Saved to ~/downloads";
      task.completed = true;
      updateActiveCount();
      loadCompletedFiles();
    } else if (status === "FAILED") {
      task.badge.className = "badge badge-failed";
      task.badge.textContent = "Failed";
      task.speed.textContent = msg || "Error";
      task.completed = true;
      updateActiveCount();
    }
  } else if (raw.startsWith("FILENAME:")) {
    const parts = raw.split(":");
    const taskId = parts[1];
    const filename = parts.slice(2).join(":");
    const task = getOrCreateTask(taskId);
    task.name.textContent = filename;
  } else if (raw.startsWith("PROGRESS:")) {
    const parts = raw.split(":");
    const taskId = parts[1];
    const progStr = parts.slice(2).join(":");
    const task = getOrCreateTask(taskId);

    task.badge.className = "badge badge-downloading";
    task.badge.textContent = "Downloading";

    const p = parseAria(progStr);
    if (p.percent !== null) {
      task.fill.style.width = `${p.percent}%`;
      task.pct.textContent = `${p.percent}%`;
    }
    if (p.speed) {
      task.speed.textContent = `Speed: ${p.speed} | CN: ${p.cn || 16} | ETA: ${p.eta || '--'}`;
    }
  }
};

async function submitBatch() {
  const input = document.getElementById('targetUrls');
  const raw = input.value.trim();
  if (!raw) return;

  const urls = raw.split(/\n+/).map(u => u.trim()).filter(u => u.length > 0);
  if (urls.length === 0) return;

  input.value = '';
  toggleInputCard();

  const res = await fetch('/api/run-batch', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ urls })
  });

  const data = await res.json();
  if (data.tasks) {
    data.tasks.forEach(t => {
      getOrCreateTask(t.id, t.url);
    });
  }
}