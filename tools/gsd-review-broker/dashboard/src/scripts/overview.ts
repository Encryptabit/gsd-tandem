/**
 * Overview tab data fetching and SSE subscription.
 * Fetches initial data from /dashboard/api/overview and subscribes
 * to SSE overview_update events via window.gsdSSE.subscribe().
 */

interface OverviewData {
  broker: {
    version: string;
    uptime_seconds: number;
    address: string;
    config: Record<string, unknown>;
  };
  stats: {
    total_reviews: number;
    by_status: Record<string, number>;
    by_category: Record<string, number>;
    approval_rate_pct: number | null;
    avg_time_to_verdict_seconds: number | null;
    avg_review_duration_seconds: number | null;
  };
  reviewers: {
    pool_active: boolean;
    session_token: string | null;
    pool_size: number;
    reviewers: ReviewerInfo[];
  };
}

interface ReviewerInfo {
  id: string;
  display_name: string;
  status: string;
  pid: number;
  spawned_at: string;
  last_active_at: string;
  reviews_completed: number;
  total_review_seconds: number;
  approvals: number;
  rejections: number;
  current_review: string | null;
}

// Track uptime for local tick
let lastUptimeSeconds = 0;
let uptimeInterval: ReturnType<typeof setInterval> | null = null;

async function fetchOverview(): Promise<OverviewData> {
  const resp = await fetch('/dashboard/api/overview');
  if (!resp.ok) {
    throw new Error('HTTP ' + resp.status + ': ' + resp.statusText);
  }
  return resp.json();
}

function formatUptime(seconds: number): string {
  if (seconds < 0) return '--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return h + 'h ' + m + 'm ' + s + 's';
  if (m > 0) return m + 'm ' + s + 's';
  return s + 's';
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return '--';
  if (seconds < 60) return Math.round(seconds) + 's';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m + 'm ' + s + 's';
}

function escapeHtml(text: string): string {
  const div = document.createElement('div');
  div.appendChild(document.createTextNode(text));
  return div.innerHTML;
}

function renderStatus(broker: OverviewData['broker']): void {
  const dot = document.getElementById('broker-status-dot');
  const label = document.getElementById('broker-status-label');
  const version = document.getElementById('broker-version');
  const uptime = document.getElementById('broker-uptime');
  const address = document.getElementById('broker-address');
  const configGrid = document.getElementById('broker-config');

  if (dot) {
    dot.setAttribute('data-status', 'online');
  }
  if (label) {
    label.textContent = 'Online';
  }
  if (version) {
    version.textContent = 'v' + broker.version;
  }
  if (address) {
    address.textContent = broker.address;
  }

  // Update uptime and start ticking
  lastUptimeSeconds = broker.uptime_seconds;
  if (uptime) {
    uptime.textContent = formatUptime(lastUptimeSeconds);
  }
  startUptimeTick();

  // Render config items
  if (configGrid) {
    configGrid.innerHTML = '';
    const configEntries = Object.entries(broker.config);
    for (const [key, value] of configEntries) {
      const label = key.replace(/_/g, ' ');
      const display = value === null || value === undefined ? '--' : String(value);
      const item = document.createElement('div');
      item.className = 'config-item';
      item.innerHTML =
        '<span class="config-key">' + escapeHtml(label) + '</span>' +
        '<span class="config-value">' + escapeHtml(display) + '</span>';
      configGrid.appendChild(item);
    }
  }
}

function startUptimeTick(): void {
  if (uptimeInterval) return; // Already ticking
  uptimeInterval = setInterval(() => {
    lastUptimeSeconds++;
    const el = document.getElementById('broker-uptime');
    if (el) {
      el.textContent = formatUptime(lastUptimeSeconds);
    }
  }, 1000);
}

function renderStats(stats: OverviewData['stats']): void {
  const totalEl = document.getElementById('stat-total-reviews');
  const rateEl = document.getElementById('stat-approval-rate');
  const avgEl = document.getElementById('stat-avg-time');

  if (totalEl) {
    totalEl.textContent = String(stats.total_reviews);
  }
  if (rateEl) {
    rateEl.textContent = stats.approval_rate_pct !== null
      ? Math.round(stats.approval_rate_pct) + '%'
      : '--';
  }
  if (avgEl) {
    avgEl.textContent = formatDuration(stats.avg_time_to_verdict_seconds);
  }

  // Status breakdown
  const statusKeys = ['pending', 'claimed', 'approved', 'changes_requested', 'closed'];
  for (const key of statusKeys) {
    const el = document.getElementById('status-' + key);
    if (el) {
      el.textContent = String(stats.by_status[key] || 0);
    }
  }
}

function renderReviewers(reviewers: OverviewData['reviewers']): void {
  const emptyEl = document.getElementById('reviewers-empty');
  const tableWrap = document.getElementById('reviewers-table-wrap');
  const tbody = document.getElementById('reviewers-tbody');

  if (!emptyEl || !tableWrap || !tbody) return;

  if (!reviewers.pool_active || reviewers.reviewers.length === 0) {
    emptyEl.style.display = '';
    tableWrap.style.display = 'none';
    const p = emptyEl.querySelector('p');
    if (p) {
      p.textContent = !reviewers.pool_active
        ? 'No active reviewers. Pool is idle or not configured.'
        : 'Pool is active but no reviewers are running.';
    }
    return;
  }

  emptyEl.style.display = 'none';
  tableWrap.style.display = '';

  tbody.innerHTML = '';
  for (const r of reviewers.reviewers) {
    const tr = document.createElement('tr');

    // Determine status dot class
    let dotClass = 'reviewer-dot-idle';
    if (r.status === 'active') dotClass = 'reviewer-dot-active';
    else if (r.status === 'draining') dotClass = 'reviewer-dot-draining';
    else if (r.status === 'terminated') dotClass = 'reviewer-dot-terminated';

    // Calculate uptime from spawned_at
    const spawned = new Date(r.spawned_at).getTime();
    const now = Date.now();
    const uptimeSec = Math.max(0, Math.floor((now - spawned) / 1000));

    // Current review display
    const currentReview = r.current_review
      ? escapeHtml(r.current_review)
      : '<span class="reviewer-current-none">--</span>';

    tr.innerHTML =
      '<td>' + escapeHtml(r.display_name) + '</td>' +
      '<td><span class="reviewer-status"><span class="reviewer-dot ' + dotClass + '"></span>' + escapeHtml(r.status) + '</span></td>' +
      '<td>' + r.pid + '</td>' +
      '<td>' + currentReview + '</td>' +
      '<td class="reviewer-col-right">' + r.reviews_completed + '</td>' +
      '<td class="reviewer-col-right">' + r.approvals + '</td>' +
      '<td>' + formatUptime(uptimeSec) + '</td>';
    tbody.appendChild(tr);
  }
}

function showError(): void {
  const dot = document.getElementById('broker-status-dot');
  const label = document.getElementById('broker-status-label');
  if (dot) {
    dot.setAttribute('data-status', 'offline');
  }
  if (label) {
    label.textContent = 'Unable to connect';
  }
}

function handleOverviewUpdate(data: unknown): void {
  const d = data as OverviewData;
  if (d.broker) renderStatus(d.broker);
  if (d.stats) renderStats(d.stats);
  if (d.reviewers) renderReviewers(d.reviewers);
}

async function init(): Promise<void> {
  // Fetch initial data
  try {
    const data = await fetchOverview();
    renderStatus(data.broker);
    renderStats(data.stats);
    renderReviewers(data.reviewers);
  } catch {
    showError();
  }

  // Subscribe to SSE updates
  if (window.gsdSSE) {
    window.gsdSSE.subscribe('overview_update', handleOverviewUpdate);
  }
}

document.addEventListener('DOMContentLoaded', init);
