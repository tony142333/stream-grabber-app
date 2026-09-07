const tasks = {};
const terminalLogs = document.getElementById('terminalLogs');
let configPanelLoaded = false;
let currentConfigs = [];
const probedPayloads = {};

function switchView(viewName, el) {
  document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
  document.querySelectorAll('.view-panel').forEach(p => p.classList.remove('active'));
  el.classList.add('active');

  document.getElementById(`panel-${viewName}`).classList.add('active');
  const titles = {
    'active': 'Active Downloads',
    'completed': 'Downloaded Files (~/downloads)',
    'terminal': 'Live Mirroring Terminal',
    'configs': 'Site Stream Configurations'
  };
  document.getElementById('viewTitle').textContent = titles[viewName];

  if (viewName === 'completed') loadCompletedFiles();
  if (viewName === 'configs') loadConfigPanel();
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

async function loadConfigPanel() {
  if (!configPanelLoaded) {
    const res = await fetch('/api/config-panel-template');
    const html = await res.text();
    document.getElementById('panel-configs').innerHTML = html;
    configPanelLoaded = true;
    toggleTitleModeControls();
  }
  await fetchConfigs();
}

async function fetchConfigs() {
  try {
    const res = await fetch('/api/configs');
    const data = await res.json();
    currentConfigs = data.sites || [];
    document.getElementById('configsBadge').textContent = currentConfigs.length;
    renderConfigGrid();
  } catch(e) {}
}
fetchConfigs();

function renderConfigGrid() {
  const container = document.getElementById('configPresetsGrid');
  if (!container) return;

  if (currentConfigs.length === 0) {
    container.innerHTML = '<div style="color: var(--text-muted); padding: 2rem; grid-column: 1/-1; text-align: center;">No site configurations created yet. Click "+ Add New Site Config" above.</div>';
    return;
  }

  container.innerHTML = currentConfigs.map(c => `
    <div class="task-card" style="gap: 0.85rem;">
      <div style="display:flex; justify-content:space-between; align-items:start;">
        <div>
          <h4 style="color:#fff; font-size: 0.95rem;">${c.name}</h4>
          <span style="font-size:0.75rem; color:var(--accent); font-family:var(--font-mono);">${c.match_domain}</span>
        </div>
        <div style="display: flex; gap: 6px;">
          <span class="badge ${c.engine_mode === 'tamperdev' ? 'badge-failed' : 'badge-downloading'}" style="text-transform:none;">
            ${c.engine_mode === 'tamperdev' ? 'TamperDev Mode' : 'Standard'}
          </span>
          <span class="badge badge-sniffing" style="text-transform:none;">${c.title_mode === 'html_tag' ? 'HTML Selector' : 'URL Regex'}</span>
        </div>
      </div>

      <div style="font-size: 0.78rem; color: var(--text-muted); font-family: var(--font-mono); background: var(--bg-input); padding: 8px; border-radius: 4px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
        ${c.example_url || 'No example URL provided'}
      </div>

      <div style="display:flex; gap: 4px; flex-wrap: wrap;">
        ${(c.quality_preference || []).map(q => `<span style="font-size: 0.7rem; background: var(--border); padding: 2px 6px; border-radius: 4px; color: var(--text-main); font-family: var(--font-mono);">${q}p</span>`).join('')}
      </div>

      <div style="display:flex; justify-content: flex-end; gap: 8px; border-top: 1px solid var(--border); padding-top: 8px; margin-top: 4px;">
        <button onclick="editConfigPreset('${c.id}')" style="background: transparent; color: var(--accent); border: 1px solid var(--border); padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 0.75rem;">Edit</button>
        <button onclick="deleteConfigPreset('${c.id}')" style="background: transparent; color: var(--danger); border: 1px solid var(--border); padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 0.75rem;">Delete</button>
      </div>
    </div>
  `).join('');
}

function toggleTitleModeControls() {
  const mode = document.getElementById('cfgTitleMode');
  if (!mode) return;
  const label = document.getElementById('lblTitlePattern');
  const input = document.getElementById('cfgTitlePattern');

  if (mode.value === 'html_tag') {
    label.textContent = 'Target HTML CSS Selector';
    input.placeholder = 'e.g., h1.video-title, .video-header h2';
  } else {
    label.textContent = 'Target Page URL Regex Pattern (Capturing Group $1)';
    input.placeholder = 'e.g., /watch/([^/]+)\\.html';
  }
}

function openConfigModal() {
  document.getElementById('formDrawerTitle').textContent = "Create Site Rule";
  document.getElementById('cfgId').value = "cfg_" + Math.random().toString(36).substr(2, 6);
  document.getElementById('lblConfigId').textContent = "Auto-generated ID";
  document.getElementById('cfgName').value = "";
  document.getElementById('cfgDomain').value = "";
  document.getElementById('cfgExample').value = "";
  document.getElementById('cfgEngineMode').value = "standard";
  document.getElementById('cfgTitleMode').value = "page_url";
  document.getElementById('cfgTitlePattern').value = "";
  document.getElementById('cfgKeywords').value = ".mp4, .m3u8";
  document.getElementById('cfgClicks').value = ".fluid_initial_play_button, #player, .play";
  document.getElementById('cfgQualities').value = "2160, 1080, 720, 480";

  toggleTitleModeControls();
  document.getElementById('configFormDrawer').style.display = 'flex';
}

function editConfigPreset(id) {
  const c = currentConfigs.find(item => item.id === id);
  if (!c) return;

  document.getElementById('formDrawerTitle').textContent = `Edit Site Rule: ${c.name}`;
  document.getElementById('cfgId').value = c.id;
  document.getElementById('lblConfigId').textContent = `ID: ${c.id}`;
  document.getElementById('cfgName').value = c.name || "";
  document.getElementById('cfgDomain').value = c.match_domain || "";
  document.getElementById('cfgExample').value = c.example_url || "";
  document.getElementById('cfgEngineMode').value = c.engine_mode || "standard";
  document.getElementById('cfgTitleMode').value = c.title_mode || "page_url";
  document.getElementById('cfgTitlePattern').value = "";
  document.getElementById('cfgKeywords').value = (c.sniff_keywords || []).join(", ");
  document.getElementById('cfgClicks').value = (c.click_selectors || []).join(", ");
  document.getElementById('cfgQualities').value = (c.quality_preference || []).join(", ");

  toggleTitleModeControls();
  document.getElementById('configFormDrawer').style.display = 'flex';
}

function closeConfigModal() {
  document.getElementById('configFormDrawer').style.display = 'none';
}

async function submitConfigPreset() {
  const payload = {
    id: document.getElementById('cfgId').value,
    name: document.getElementById('cfgName').value.trim(),
    match_domain: document.getElementById('cfgDomain').value.trim(),
    example_url: document.getElementById('cfgExample').value.trim(),
    engine_mode: document.getElementById('cfgEngineMode').value,
    title_mode: document.getElementById('cfgTitleMode').value,
    title_pattern: document.getElementById('cfgTitlePattern').value.trim(),
    sniff_keywords: document.getElementById('cfgKeywords').value.split(',').map(s => s.trim()).filter(Boolean),
    click_selectors: document.getElementById('cfgClicks').value.split(',').map(s => s.trim()).filter(Boolean),
    quality_preference: document.getElementById('cfgQualities').value.split(',').map(s => s.trim()).filter(Boolean)
  };

  if (!payload.name || !payload.match_domain) {
    alert("Please provide a Site Display Name and Match Domain.");
    return;
  }

  await fetch('/api/configs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });

  closeConfigModal();
  await fetchConfigs();
}

async function deleteConfigPreset(id) {
  if (!confirm("Are you sure you want to delete this site configuration profile?")) return;
  await fetch(`/api/configs/${id}`, { method: 'DELETE' });
  await fetchConfigs();
}

function getOrCreateTask(taskId, initialName = "Sniffing stream from source...") {
  if (tasks[taskId]) return tasks[taskId];
  const noMsg = document.getElementById('noActiveMsg');
  if (noMsg) noMsg.style.display = 'none';

  const card = document.createElement('div');
  card.className = 'task-card';
  card.id = `task-${taskId}`;
  card.style.transition = 'opacity 0.4s ease, transform 0.4s ease';
  card.innerHTML = `
    <div class="task-header" style="display:flex; justify-content:space-between; align-items:center;">
      <div class="task-name" id="name-${taskId}" style="word-break: break-all; max-width: 70%;">${initialName}</div>
      <div style="display: flex; gap: 8px; align-items: center;">
        <span class="badge badge-sniffing" id="badge-${taskId}">Searching</span>
        <div id="actions-${taskId}" style="display: none; gap: 6px;"></div>
      </div>
    </div>
    <div class="progress-bar-track">
      <div class="progress-bar-fill" id="fill-${taskId}"></div>
    </div>
    <div class="task-metrics" style="display:flex; justify-content:space-between; font-size:0.75rem;">
      <span id="speed-${taskId}">Initializing browser session...</span>
      <span id="size-${taskId}" style="color: var(--accent); font-family: var(--font-mono);"></span>
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
    size: card.querySelector(`#size-${taskId}`),
    pct: card.querySelector(`#pct-${taskId}`),
    actions: card.querySelector(`#actions-${taskId}`),
    status: "SEARCHING",
    isPaused: false
  };
  updateActiveCount();
  return tasks[taskId];
}

function renderTaskActionControls(taskId, show = true) {
  const task = tasks[taskId];
  if (!task || !task.actions) return;

  if (!show) {
    task.actions.style.display = "none";
    return;
  }

  task.actions.style.display = "flex";
  task.actions.innerHTML = `
    <button id="btnPause-${taskId}" onclick="togglePauseTask('${taskId}')" style="background: #1e293b; color: #f8fafc; border: 1px solid #334155; padding: 3px 8px; border-radius: 4px; font-size: 0.72rem; cursor: pointer;">
      ${task.isPaused ? "▶ Resume" : "⏸ Pause"}
    </button>
    <button onclick="stopTask('${taskId}')" style="background: #ef444420; color: #f87171; border: 1px solid #ef444440; padding: 3px 8px; border-radius: 4px; font-size: 0.72rem; cursor: pointer;">
      ⏹ Stop
    </button>
  `;
}

async function togglePauseTask(taskId) {
  const task = tasks[taskId];
  if (!task) return;

  const endpoint = task.isPaused ? `/api/task/${taskId}/resume` : `/api/task/${taskId}/pause`;
  await fetch(endpoint, { method: 'POST' });
}

async function stopTask(taskId) {
  await fetch(`/api/task/${taskId}/stop`, { method: 'POST' });
}

function removeTaskCard(taskId) {
  const task = tasks[taskId];
  if (!task || !task.element) return;

  task.element.style.opacity = '0';
  task.element.style.transform = 'translateY(-10px)';

  setTimeout(() => {
    if (task.element.parentNode) {
      task.element.parentNode.removeChild(task.element);
    }
    delete tasks[taskId];
    updateActiveCount();
  }, 400);
}

function updateActiveCount() {
  const allTasks = Object.values(tasks);
  const activeDownloading = allTasks.filter(t => t.status === "DOWNLOADING" || t.status === "SEARCHING" || t.status === "PAUSED").length;
  document.getElementById('activeBadge').textContent = activeDownloading;

  const hasFinished = allTasks.some(t => t.status === "COMPLETED");
  const hasStopped = allTasks.some(t => t.status === "STOPPED" || t.status === "FAILED");

  const btnFin = document.getElementById('btnClearFinished');
  const btnStp = document.getElementById('btnClearStopped');
  if (btnFin) btnFin.style.display = hasFinished ? "inline-block" : "none";
  if (btnStp) btnStp.style.display = hasStopped ? "inline-block" : "none";

  const noMsg = document.getElementById('noActiveMsg');
  if (noMsg) {
    noMsg.style.display = allTasks.length === 0 ? 'block' : 'none';
  }
}

function clearTasksByStatus(...targetStatuses) {
  Object.keys(tasks).forEach(tId => {
    if (targetStatuses.includes(tasks[tId].status)) {
      removeTaskCard(tId);
    }
  });
}

function parseAria(str) {
  const pctMatch = str.match(/\(([0-9]+)%\)/);
  const dlMatch = str.match(/DL:([^\s]+)/);
  const etaMatch = str.match(/ETA:([^\s\]]+)/);
  const cnMatch = str.match(/CN:([0-9]+)/);
  const sizeMatch = str.match(/([0-9.]+[KMGTP]?i?B)\/([0-9.]+[KMGTP]?i?B)/i);

  return {
    percent: pctMatch ? parseInt(pctMatch[1]) : null,
    speed: dlMatch ? dlMatch[1] : null,
    eta: etaMatch ? etaMatch[1] : null,
    cn: cnMatch ? cnMatch[1] : null,
    size: sizeMatch ? `${sizeMatch[1]} / ${sizeMatch[2]}` : null
  };
}

async function triggerSelectedDownload(taskId) {
  const payload = probedPayloads[taskId];
  if (!payload) return;

  const selectEl = document.getElementById(`sel-${taskId}`);
  const chosenQuality = selectEl ? selectEl.value : (payload.qualities[0] || '1080');

  let streamUrl = payload.base_stream;
  if (streamUrl.includes("{quality}")) {
    streamUrl = streamUrl.replace("{quality}", chosenQuality);
  } else {
    streamUrl = streamUrl.replace(/([-_/])(360|480|720|1080|1440|2160)(\.mp4|p\.mp4|\?)/, `$1${chosenQuality}$3`);
  }

  const ctrl = document.getElementById(`quality-ctrl-${taskId}`);
  if (ctrl) ctrl.remove();

  const task = getOrCreateTask(taskId);
  task.status = "DOWNLOADING";
  task.badge.className = "badge badge-downloading";
  task.badge.textContent = "Downloading";
  task.speed.textContent = `Starting ${chosenQuality}p download via aria2c...`;

  renderTaskActionControls(taskId, true);

  await fetch('/api/start-download', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      task_id: taskId,
      target_stream_url: streamUrl,
      output_filename: payload.filename,
      referer: payload.referer,
      cookies: payload.cookies || ""
    })
  });
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
    task.status = status;

    if (status === "SEARCHING") {
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Searching";
      task.speed.textContent = msg || "Navigating to target page...";
      renderTaskActionControls(taskId, false);
    } else if (status === "FOUND") {
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Found";
      task.speed.textContent = msg || "Testing resolutions...";
    } else if (status === "AWAITING_SELECTION") {
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Select Quality";
      task.speed.textContent = "Choose quality below";
    } else if (status === "DOWNLOADING") {
      task.isPaused = false;
      task.badge.className = "badge badge-downloading";
      task.badge.textContent = "Downloading";
      task.speed.textContent = msg || "Downloading stream...";
      renderTaskActionControls(taskId, true);
    } else if (status === "PAUSED") {
      task.isPaused = true;
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Paused";
      task.speed.textContent = "Paused by user";
      renderTaskActionControls(taskId, true);
    } else if (status === "STOPPED") {
      task.badge.className = "badge badge-failed";
      task.badge.textContent = "Stopped";
      task.speed.textContent = "Download cancelled";
      renderTaskActionControls(taskId, false);
      updateActiveCount();
    } else if (status === "COMPLETED") {
      task.badge.className = "badge badge-completed";
      task.badge.textContent = "Finished";
      task.fill.style.width = "100%";
      task.pct.textContent = "100%";
      task.speed.textContent = "Saved to ~/downloads";
      renderTaskActionControls(taskId, false);

      updateActiveCount();
      loadCompletedFiles();
    } else if (status === "FAILED") {
      task.badge.className = "badge badge-failed";
      task.badge.textContent = "Failed";
      task.speed.textContent = msg || "Error occurred";
      renderTaskActionControls(taskId, false);
      updateActiveCount();
    }
  } else if (raw.startsWith("FILENAME:")) {
    const parts = raw.split(":");
    const taskId = parts[1];
    const filename = parts.slice(2).join(":");
    const task = getOrCreateTask(taskId);
    task.name.textContent = filename;
  } else if (raw.startsWith("PROBE_DATA:")) {
    const firstColon = raw.indexOf(":");
    const secondColon = raw.indexOf(":", firstColon + 1);
    const taskId = raw.substring(firstColon + 1, secondColon);
    const jsonStr = raw.substring(secondColon + 1);

    try {
      const payload = JSON.parse(jsonStr);
      probedPayloads[taskId] = payload;

      const task = getOrCreateTask(taskId);
      task.name.textContent = payload.filename || task.name.textContent;
      task.badge.className = "badge badge-sniffing";
      task.badge.textContent = "Select Quality";
      task.speed.textContent = "Choose resolution below to proceed";

      if (!document.getElementById(`quality-ctrl-${taskId}`)) {
        const container = document.createElement("div");
        container.id = `quality-ctrl-${taskId}`;
        container.style = "display: flex; gap: 10px; margin-top: 12px; align-items: center;";
        container.innerHTML = `
          <select id="sel-${taskId}" style="background: #1e293b; color: #f8fafc; border: 1px solid #334155; padding: 7px 12px; border-radius: 6px; font-family: monospace; font-size: 0.85rem; outline: none; cursor: pointer;">
            ${payload.qualities.map((q, idx) => `
              <option value="${q}" ${idx === 0 ? 'selected' : ''}>
                ${q}p ${idx === 0 ? '(Highest)' : ''}
              </option>
            `).join('')}
          </select>
          <button style="background: #3b82f6; color: #fff; border: none; padding: 7px 16px; border-radius: 6px; font-weight: 600; font-size: 0.85rem; cursor: pointer;" onclick="triggerSelectedDownload('${taskId}')">Download</button>
        `;
        task.element.appendChild(container);
      }
    } catch(err) {
      console.error("Failed to parse probe data", err);
    }
  } else if (raw.startsWith("PROGRESS:")) {
    const parts = raw.split(":");
    const taskId = parts[1];
    const progStr = parts.slice(2).join(":");
    const task = getOrCreateTask(taskId);

    if (task.status !== "PAUSED" && task.status !== "STOPPED") {
      task.badge.className = "badge badge-downloading";
      task.badge.textContent = "Downloading";
    }

    const p = parseAria(progStr);
    if (p.percent !== null) {
      task.fill.style.width = `${p.percent}%`;
      task.pct.textContent = `${p.percent}%`;
    }
    if (p.size) {
      task.size.textContent = p.size;
    }
    if (p.speed) {
      task.speed.textContent = `Speed: ${p.speed} | ETA: ${p.eta || '--'}`;
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