/**
 * Log viewer tab data fetching, rendering, SSE tail, search filtering.
 * Fetches log file list from /dashboard/api/logs, loads file contents,
 * renders terminal-style entries, and subscribes to SSE for live tail.
 */

interface LogFile {
  name: string;
  size: number;
  modified: string;
  source: 'broker' | 'reviewer';
}

interface LogEntry {
  ts?: string;
  level?: string;
  event?: string;
  message?: string;
  reviewer_id?: string;
  caller_tag?: string;
  session_token?: string;
  stream?: string;
  pid?: number;
  exit_code?: number;
  exception?: string;
  [key: string]: unknown;
}

// State
let currentFile: string | null = null;
let allEntries: LogEntry[] = [];
let tailActive: boolean = true;
let searchQuery: string = '';
let autoScroll: boolean = true;
let tailEventSource: EventSource | null = null;
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null;
let tailIdleTimer: ReturnType<typeof setTimeout> | null = null;
let fileRefreshTimer: ReturnType<typeof setInterval> | null = null;
let lastFileListSignature: string = '';
let fileListSyncInFlight: boolean = false;

const FILE_LIST_REFRESH_INTERVAL_MS = 5000;

function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(text));
  return div.innerHTML;
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function formatTimestamp(iso: string): string {
  try {
    const d = new Date(iso);
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    return h + ':' + m + ':' + s + '.' + ms;
  } catch {
    return iso || '--';
  }
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    const mon = months[d.getMonth()];
    const day = d.getDate();
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    return mon + ' ' + day + ', ' + hh + ':' + mm;
  } catch {
    return iso || '--';
  }
}

function determineLevel(entry: LogEntry): string {
  if (entry.level) return entry.level.toLowerCase();
  // Infer from reviewer log events
  if (entry.event === 'reviewer_exit' && entry.exit_code !== undefined && entry.exit_code !== 0) {
    return 'error';
  }
  if (entry.event === 'stderr_line' || entry.stream === 'stderr') {
    return 'warn';
  }
  return 'info';
}

function getTag(entry: LogEntry): string {
  if (entry.caller_tag) return entry.caller_tag;
  if (entry.reviewer_id) return entry.reviewer_id;
  if (entry.event) return entry.event;
  return '';
}

function matchesSearch(entry: LogEntry, query: string): boolean {
  if (!query) return true;
  const lowerQuery = query.toLowerCase();
  const jsonStr = JSON.stringify(entry).toLowerCase();
  return jsonStr.indexOf(lowerQuery) !== -1;
}

function createEntryElement(entry: LogEntry): HTMLDivElement {
  const div = document.createElement('div');
  div.className = 'log-entry';

  const level = determineLevel(entry);
  div.classList.add('log-level-' + level);

  const tag = getTag(entry);
  const ts = entry.ts ? formatTimestamp(entry.ts) : '--:--:--.---';
  const msg = entry.message || '';

  let headerHtml = '<span class="log-entry-ts">[' + escapeHtml(ts) + ']</span>';
  if (tag) {
    headerHtml += ' <span class="log-entry-tag">[' + escapeHtml(tag) + ']</span>';
  }
  if (msg) {
    headerHtml += ' <span class="log-entry-message">' + escapeHtml(msg) + '</span>';
  }

  div.innerHTML = headerHtml;
  return div;
}

async function fetchFileList(): Promise<LogFile[]> {
  const resp = await fetch('/dashboard/api/logs');
  if (!resp.ok) {
    throw new Error('HTTP ' + resp.status + ': ' + resp.statusText);
  }
  const data = await resp.json();
  return data.files || [];
}

function populateDropdown(files: LogFile[], preferredFile: string | null): string | null {
  const select = document.getElementById('log-file-select') as HTMLSelectElement | null;
  if (!select) return null;

  // Remove all children except the first placeholder option
  while (select.children.length > 1) {
    select.removeChild(select.lastChild!);
  }

  if (files.length === 0) {
    const noFiles = document.createElement('option');
    noFiles.value = '';
    noFiles.disabled = true;
    noFiles.selected = true;
    noFiles.textContent = 'No log files found';
    select.appendChild(noFiles);
    return null;
  }

  const brokerFiles = files.filter(function(f) { return f.source === 'broker'; });
  const reviewerFiles = files.filter(function(f) { return f.source === 'reviewer'; });

  if (brokerFiles.length > 0) {
    const brokerGroup = document.createElement('optgroup');
    brokerGroup.label = 'Broker Logs';
    for (let i = 0; i < brokerFiles.length; i++) {
      const f = brokerFiles[i];
      const opt = document.createElement('option');
      opt.value = f.name;
      opt.textContent = f.name + ' (' + formatSize(f.size) + ', ' + formatDate(f.modified) + ')';
      brokerGroup.appendChild(opt);
    }
    select.appendChild(brokerGroup);
  }

  if (reviewerFiles.length > 0) {
    const reviewerGroup = document.createElement('optgroup');
    reviewerGroup.label = 'Reviewer Logs';
    for (let j = 0; j < reviewerFiles.length; j++) {
      const rf = reviewerFiles[j];
      const ropt = document.createElement('option');
      ropt.value = rf.name;
      ropt.textContent = rf.name + ' (' + formatSize(rf.size) + ', ' + formatDate(rf.modified) + ')';
      reviewerGroup.appendChild(ropt);
    }
    select.appendChild(reviewerGroup);
  }

  const hasPreferred = preferredFile
    ? files.some(function(f) { return f.name === preferredFile; })
    : false;

  if (hasPreferred && preferredFile) {
    select.value = preferredFile;
    return preferredFile;
  }

  // Auto-select most recent file (API returns sorted by mtime desc)
  select.value = files[0].name;
  return files[0].name;
}

function buildFileListSignature(files: LogFile[]): string {
  if (files.length === 0) return 'empty';
  return files
    .map(function(f) {
      return f.source + ':' + f.name + ':' + f.size + ':' + f.modified;
    })
    .join('|');
}

async function syncFileList(): Promise<void> {
  if (fileListSyncInFlight) return;
  fileListSyncInFlight = true;

  try {
    const files = await fetchFileList();
    const signature = buildFileListSignature(files);
    if (signature === lastFileListSignature) return;

    const select = document.getElementById('log-file-select') as HTMLSelectElement | null;
    const preferredFile = currentFile || (select && select.value ? select.value : null);
    const selectedFile = populateDropdown(files, preferredFile);
    lastFileListSignature = signature;

    if (!selectedFile) {
      currentFile = null;
      stopTail();
      return;
    }

    if (selectedFile !== currentFile) {
      await loadFile(selectedFile);
    }
  } catch {
    // Keep current dropdown and view state on transient refresh failures.
  } finally {
    fileListSyncInFlight = false;
  }
}

function renderEntries(): void {
  const output = document.getElementById('log-output');
  if (!output) return;
  output.innerHTML = '';

  const filtered = allEntries.filter(function(e) {
    return matchesSearch(e, searchQuery);
  });

  if (filtered.length === 0 && allEntries.length > 0) {
    output.innerHTML = '<div style="text-align:center;color:var(--color-text-secondary);padding:20px;">No entries match the search filter.</div>';
    return;
  }

  const fragment = document.createDocumentFragment();
  for (let i = 0; i < filtered.length; i++) {
    fragment.appendChild(createEntryElement(filtered[i]));
  }
  output.appendChild(fragment);

  if (autoScroll) {
    output.scrollTop = output.scrollHeight;
  }
}

async function loadFile(filename: string): Promise<void> {
  const output = document.getElementById('log-output');
  const empty = document.getElementById('log-empty');
  const loading = document.getElementById('log-loading');

  if (loading) loading.style.display = '';
  if (output) output.style.display = 'none';
  if (empty) empty.style.display = 'none';

  // Stop any existing tail
  stopTail();
  currentFile = filename;
  allEntries = [];

  try {
    const resp = await fetch('/dashboard/api/logs/' + encodeURIComponent(filename));
    if (!resp.ok) {
      throw new Error('HTTP ' + resp.status);
    }
    const data = await resp.json();
    allEntries = data.entries || [];
  } catch {
    allEntries = [];
    if (output) {
      output.innerHTML = '<div style="text-align:center;color:var(--color-error);padding:20px;">Failed to load log file.</div>';
    }
  }

  if (loading) loading.style.display = 'none';
  if (output) output.style.display = '';

  renderEntries();

  if (output) {
    output.scrollTop = output.scrollHeight;
  }

  // Start tail if enabled
  if (tailActive && currentFile) {
    startTail(currentFile);
  }
}

function setTailDotState(state: 'active' | 'idle' | 'paused'): void {
  const dot = document.getElementById('log-tail-dot');
  if (dot) {
    dot.className = 'tail-dot tail-dot-' + state;
  }
}

function scheduleTailIdle(): void {
  if (tailIdleTimer) clearTimeout(tailIdleTimer);
  tailIdleTimer = setTimeout(function() {
    // Only fade to idle if tail is still active (not paused/stopped)
    if (tailActive && tailEventSource) {
      setTailDotState('idle');
    }
  }, 3000);
}

function startTail(filename: string): void {
  stopTail();

  // Start in idle state -- will pulse only when entries arrive
  setTailDotState('idle');

  try {
    tailEventSource = new EventSource('/dashboard/events?tail=' + encodeURIComponent(filename));

    tailEventSource.onmessage = function(event) {
      try {
        const data = JSON.parse(event.data);
        if (data.type === 'log_tail' && Array.isArray(data.entries) && data.entries.length > 0) {
          const logOutput = document.getElementById('log-output');
          if (!logOutput) return;

          // Flash dot to active (pulsing) when entries arrive
          setTailDotState('active');
          scheduleTailIdle();

          for (let i = 0; i < data.entries.length; i++) {
            const entry = data.entries[i] as LogEntry;
            allEntries.push(entry);

            if (matchesSearch(entry, searchQuery)) {
              logOutput.appendChild(createEntryElement(entry));
            }
          }

          if (autoScroll && logOutput) {
            logOutput.scrollTop = logOutput.scrollHeight;
          }
        }
      } catch {
        // Ignore non-JSON or malformed messages
      }
    };

    tailEventSource.onerror = function() {
      setTailDotState('idle');
    };

    tailEventSource.onopen = function() {
      // Connected but no entries yet -- stay idle until data arrives
      setTailDotState('idle');
    };
  } catch {
    setTailDotState('idle');
  }
}

function stopTail(): void {
  if (tailIdleTimer) {
    clearTimeout(tailIdleTimer);
    tailIdleTimer = null;
  }
  if (tailEventSource) {
    tailEventSource.close();
    tailEventSource = null;
  }
  setTailDotState('idle');
}

function handleTailToggle(): void {
  tailActive = !tailActive;
  const label = document.getElementById('log-tail-label');

  if (tailActive) {
    if (label) label.textContent = 'Live Tail';
    if (currentFile) {
      startTail(currentFile);
    }
  } else {
    stopTail();
    setTailDotState('paused');
    if (label) label.textContent = 'Tail Paused';
  }
}

function handleSearch(): void {
  const input = document.getElementById('log-search') as HTMLInputElement | null;
  if (!input) return;
  searchQuery = input.value;
  renderEntries();
}

function handleScroll(): void {
  const logOutput = document.getElementById('log-output');
  if (!logOutput) return;
  const distanceFromBottom = logOutput.scrollHeight - logOutput.scrollTop - logOutput.clientHeight;
  autoScroll = distanceFromBottom < 50;
}

async function init(): Promise<void> {
  try {
    await syncFileList();
  } catch {
    const empty = document.getElementById('log-empty');
    if (empty) {
      const p = empty.querySelector('p');
      if (p) p.textContent = 'Unable to load log files.';
    }
  }

  // Attach event listeners
  const selectEl = document.getElementById('log-file-select');
  if (selectEl) {
    selectEl.addEventListener('change', function() {
      const s = selectEl as HTMLSelectElement;
      if (s.value) loadFile(s.value);
    });
  }

  const toggleBtn = document.getElementById('log-tail-toggle');
  if (toggleBtn) {
    toggleBtn.addEventListener('click', handleTailToggle);
  }

  const searchInput = document.getElementById('log-search');
  if (searchInput) {
    searchInput.addEventListener('input', function() {
      if (searchDebounceTimer) clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(handleSearch, 200);
    });
  }

  const outputEl = document.getElementById('log-output');
  if (outputEl) {
    outputEl.addEventListener('scroll', handleScroll);
  }

  if (window.gsdSSE) {
    window.gsdSSE.subscribe('overview_update', function() {
      void syncFileList();
    });
  }

  if (!fileRefreshTimer) {
    fileRefreshTimer = setInterval(function() {
      void syncFileList();
    }, FILE_LIST_REFRESH_INTERVAL_MS);
  }
}

document.addEventListener('DOMContentLoaded', init);
