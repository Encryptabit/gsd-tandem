#!/usr/bin/env node
// Ensure gsd-review-broker is running for tandem-enabled projects.
// Called by SessionStart hook. Silent on all failures.

const fs = require('fs');
const path = require('path');
const os = require('os');
const net = require('net');
const { spawn, spawnSync } = require('child_process');

const BROKER_PORT = parseInt(process.env.GSD_BROKER_PORT || '8321', 10);
const BROKER_HOST = process.env.GSD_BROKER_HOST || '127.0.0.1';
const SPAWN_COOLDOWN_MS = 15_000;

function safeJson(filePath) {
  try {
    if (!fs.existsSync(filePath)) return null;
    return JSON.parse(fs.readFileSync(filePath, 'utf8'));
  } catch {
    return null;
  }
}

function reviewEnabledForProject(cwd) {
  const configPath = path.join(cwd, '.planning', 'config.json');
  const cfg = safeJson(configPath);
  if (!cfg || typeof cfg !== 'object') return false;
  if (!cfg.review || typeof cfg.review !== 'object') return false;
  return cfg.review.enabled !== false;
}

function isMcpConfigured(configDir) {
  const settingsPath = path.join(configDir, 'settings.json');
  const settings = safeJson(settingsPath);
  if (!settings || typeof settings !== 'object') return false;
  const servers = settings.mcpServers;
  if (!servers || typeof servers !== 'object') return false;
  return Object.prototype.hasOwnProperty.call(servers, 'gsdreview');
}

function uniquePaths(paths) {
  const seen = new Set();
  const result = [];
  for (const p of paths) {
    const resolved = path.resolve(p);
    if (seen.has(resolved)) continue;
    seen.add(resolved);
    result.push(resolved);
  }
  return result;
}

function firstPath(paths, matcher) {
  for (const p of paths) {
    if (matcher(p)) return p;
  }
  return null;
}

function canRunUv() {
  try {
    const result = spawnSync('uv', ['--version'], {
      stdio: 'ignore',
      timeout: 1500,
      windowsHide: true,
    });
    return result.status === 0;
  } catch {
    return false;
  }
}

function isBrokerListening(host, port) {
  return new Promise(resolve => {
    const socket = new net.Socket();
    let settled = false;
    const done = value => {
      if (settled) return;
      settled = true;
      try {
        socket.destroy();
      } catch {}
      resolve(value);
    };
    socket.setTimeout(350);
    socket.once('connect', () => done(true));
    socket.once('timeout', () => done(false));
    socket.once('error', () => done(false));
    socket.connect(port, host);
  });
}

function shouldThrottle(cacheFile) {
  const cache = safeJson(cacheFile);
  if (!cache || typeof cache !== 'object') return false;
  const lastSpawnAt = Number(cache.last_spawn_at || 0);
  if (!Number.isFinite(lastSpawnAt)) return false;
  return Date.now() - lastSpawnAt < SPAWN_COOLDOWN_MS;
}

function writeSpawnCache(cacheFile, payload) {
  try {
    fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
    fs.writeFileSync(cacheFile, JSON.stringify(payload), 'utf8');
  } catch {}
}

async function main() {
  if (process.env.GSD_BROKER_AUTOSTART === '0') return;

  const cwd = process.cwd();
  const homeDir = os.homedir();
  const candidateConfigDirs = uniquePaths([
    path.resolve(__dirname, '..'),
    path.join(cwd, '.claude'),
    path.join(homeDir, '.claude'),
  ]);
  const cacheFile = path.join(homeDir, '.claude', 'cache', 'gsd-broker-autostart.json');

  if (!reviewEnabledForProject(cwd)) return;
  if (!firstPath(candidateConfigDirs, isMcpConfigured)) return;

  const brokerDir = firstPath(candidateConfigDirs, configDir => {
    const pyproject = path.join(configDir, 'tools', 'gsd-review-broker', 'pyproject.toml');
    return fs.existsSync(pyproject);
  });
  if (!brokerDir) return;
  const brokerProjectDir = path.join(brokerDir, 'tools', 'gsd-review-broker');

  if (!canRunUv()) return;
  if (await isBrokerListening(BROKER_HOST, BROKER_PORT)) return;
  if (shouldThrottle(cacheFile)) return;

  const child = spawn(
    'uv',
    ['--directory', brokerProjectDir, 'run', 'gsd-review-broker'],
    {
      detached: true,
      stdio: 'ignore',
      windowsHide: true,
      env: {
        ...process.env,
        BROKER_REPO_ROOT: cwd,
      },
    }
  );
  child.unref();

  writeSpawnCache(cacheFile, {
    last_spawn_at: Date.now(),
    pid: child.pid || null,
    repo_root: cwd,
    broker_dir: brokerProjectDir,
  });
}

main().catch(() => {});
