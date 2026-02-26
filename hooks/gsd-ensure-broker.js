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

function collectUpwardFiles(startDir, filename) {
  const files = [];
  let current = path.resolve(startDir);
  while (true) {
    files.push(path.join(current, filename));
    const parent = path.dirname(current);
    if (parent === current) break;
    current = parent;
  }
  return files;
}

function resolveProjectContext(startDir) {
  for (const configPath of collectUpwardFiles(startDir, path.join('.planning', 'config.json'))) {
    const cfg = safeJson(configPath);
    if (!cfg || typeof cfg !== 'object') continue;
    if (!cfg.review || typeof cfg.review !== 'object') continue;
    return {
      config: cfg,
      configPath,
      projectRoot: path.dirname(path.dirname(configPath)),
    };
  }
  return null;
}

function resolveWorkspaceSeedContext(startDir) {
  const workspaceRoot = path.resolve(startDir);
  let entries;
  try {
    entries = fs.readdirSync(workspaceRoot, { withFileTypes: true });
  } catch {
    return null;
  }
  const sortedDirs = entries
    .filter(entry => entry.isDirectory())
    .map(entry => entry.name)
    .sort((a, b) => a.localeCompare(b));

  for (const dirName of sortedDirs) {
    const configPath = path.join(workspaceRoot, dirName, '.planning', 'config.json');
    const cfg = safeJson(configPath);
    if (!cfg || typeof cfg !== 'object') continue;
    if (!cfg.review || typeof cfg.review !== 'object') continue;
    if (cfg.review.enabled === false) continue;
    if (!cfg.reviewer_pool || typeof cfg.reviewer_pool !== 'object') continue;
    const workspaceConfigPath = ensureWorkspaceDefaultConfig(workspaceRoot, configPath);
    if (!workspaceConfigPath) continue;
    return {
      config: cfg,
      configPath: workspaceConfigPath,
      projectRoot: workspaceRoot,
    };
  }

  return null;
}

function resolveBrokerUserConfigDir() {
  const xdgConfigHome = process.env.XDG_CONFIG_HOME;
  if (xdgConfigHome) return path.join(path.resolve(xdgConfigHome), 'gsd-review-broker');

  if (process.platform === 'win32') {
    if (process.env.APPDATA) return path.join(path.resolve(process.env.APPDATA), 'gsd-review-broker');
    return path.join(os.homedir(), 'AppData', 'Roaming', 'gsd-review-broker');
  }

  if (process.platform === 'darwin') {
    return path.join(os.homedir(), 'Library', 'Application Support', 'gsd-review-broker');
  }

  return path.join(os.homedir(), '.config', 'gsd-review-broker');
}

function copyObject(input) {
  if (!input || typeof input !== 'object' || Array.isArray(input)) return {};
  const out = {};
  for (const [key, value] of Object.entries(input)) out[key] = value;
  return out;
}

function ensureWorkspaceDefaultConfig(workspaceRoot, seedConfigPath) {
  try {
    const configDir = resolveBrokerUserConfigDir();
    fs.mkdirSync(configDir, { recursive: true });
    const configPath = path.join(configDir, 'workspace-default-config.json');

    const seed = safeJson(seedConfigPath);
    const existing = safeJson(configPath);
    let reviewerPool = {};
    if (seed && typeof seed === 'object' && seed.reviewer_pool && typeof seed.reviewer_pool === 'object') {
      reviewerPool = copyObject(seed.reviewer_pool);
    } else if (
      existing &&
      typeof existing === 'object' &&
      existing.reviewer_pool &&
      typeof existing.reviewer_pool === 'object'
    ) {
      reviewerPool = copyObject(existing.reviewer_pool);
    }

    reviewerPool.workspace_path = workspaceRoot;
    if (!reviewerPool.prompt_template_path) {
      reviewerPool.prompt_template_path = 'reviewer_prompt.md';
    }

    const payload = { reviewer_pool: reviewerPool };
    fs.writeFileSync(configPath, JSON.stringify(payload, null, 2), 'utf8');
    return configPath;
  } catch {
    return null;
  }
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

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForBrokerReady(host, port, timeoutMs = 2000) {
  const startedAt = Date.now();
  while (Date.now() - startedAt < timeoutMs) {
    if (await isBrokerListening(host, port)) return true;
    await delay(200);
  }
  return false;
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
  const projectContext = resolveProjectContext(cwd);
  const resolvedContext =
    projectContext && projectContext.config.review.enabled !== false
      ? projectContext
      : resolveWorkspaceSeedContext(cwd);
  if (!resolvedContext) return;

  const brokerRepoRoot = resolvedContext.projectRoot;
  const brokerConfigPath = resolvedContext.configPath;
  const homeDir = os.homedir();
  const candidateConfigDirs = uniquePaths([
    path.resolve(__dirname, '..'),
    path.join(brokerRepoRoot, '.claude'),
    path.join(cwd, '.claude'),
    path.join(homeDir, '.claude'),
  ]);
  const cacheFile = path.join(homeDir, '.claude', 'cache', 'gsd-broker-autostart.json');

  const brokerDir = firstPath(candidateConfigDirs, configDir => {
    const pyproject = path.join(configDir, 'tools', 'gsd-review-broker', 'pyproject.toml');
    return fs.existsSync(pyproject);
  });
  if (!brokerDir) return;
  const brokerProjectDir = path.join(brokerDir, 'tools', 'gsd-review-broker');

  if (!canRunUv()) return;
  if (await isBrokerListening(BROKER_HOST, BROKER_PORT)) return;
  if (shouldThrottle(cacheFile)) return;

  let child;
  try {
    const spawnOptions = {
      detached: process.platform !== 'win32',
      stdio: 'ignore',
      windowsHide: true,
      env: {
        ...process.env,
        BROKER_REPO_ROOT: brokerRepoRoot,
        BROKER_CONFIG_PATH: brokerConfigPath,
      },
    };
    child = spawn(
      'uv',
      ['--directory', brokerProjectDir, 'run', 'gsd-review-broker'],
      spawnOptions
    );
  } catch {
    return;
  }
  child.unref();
  await waitForBrokerReady(
    process.env.GSD_BROKER_HOST || process.env.BROKER_HOST || BROKER_HOST,
    BROKER_PORT
  );

  writeSpawnCache(cacheFile, {
    last_spawn_at: Date.now(),
    pid: child.pid || null,
    repo_root: brokerRepoRoot,
    config_path: brokerConfigPath,
    broker_dir: brokerProjectDir,
  });
}

main().catch(() => {});
