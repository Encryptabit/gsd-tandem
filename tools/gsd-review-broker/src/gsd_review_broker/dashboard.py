"""Dashboard HTTP routes for the GSD Review Broker.

Serves a self-contained HTML dashboard at /dashboard with SSE-based
connection health monitoring at /dashboard/events.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import AsyncGenerator

from starlette.requests import Request
from starlette.responses import HTMLResponse, StreamingResponse

from gsd_review_broker import __version__


# Unicode icons for sidebar nav (clipboard, scroll, magnifying glass, desktop)
_ICON_OVERVIEW = "📋"
_ICON_LOGS = "📜"
_ICON_REVIEWS = "🔍"
_ICON_POOL = "🖥"
_ICON_SUN = "☀️"
_ICON_MOON = "🌙"


_CSS = '/* === CSS Custom Properties (Design System) === */\n:root {\n  --bg: #1a1a2e;\n  --surface: #16213e;\n  --sidebar-bg: #0f3460;\n  --text-primary: #e0e0e0;\n  --text-secondary: #a0a0a0;\n  --accent: #00d4ff;\n  --accent-hover: #00e5ff;\n  --accent-glow: rgba(0, 212, 255, 0.15);\n  --border: rgba(0, 212, 255, 0.2);\n  --font-mono: \'Cascadia Code\', \'JetBrains Mono\', \'Fira Code\', \'SF Mono\', \'Consolas\', monospace;\n}\n\n[data-theme="light"] {\n  --bg: #f5f5f5;\n  --surface: #ffffff;\n  --sidebar-bg: #e8e8e8;\n  --text-primary: #1a1a1a;\n  --text-secondary: #555555;\n  --accent: #0088aa;\n  --accent-hover: #006688;\n  --accent-glow: rgba(0, 136, 170, 0.1);\n  --border: rgba(0, 136, 170, 0.3);\n}\n\n/* === Reset & Base === */\n*, *::before, *::after {\n  margin: 0;\n  padding: 0;\n  box-sizing: border-box;\n}\n\nhtml, body {\n  height: 100%;\n  font-family: var(--font-mono);\n  background: var(--bg);\n  color: var(--text-primary);\n  overflow: hidden;\n}\n\n/* === Layout === */\n.app {\n  display: flex;\n  height: 100vh;\n  width: 100vw;\n}\n\n/* === Sidebar === */\n.sidebar {\n  width: 220px;\n  min-width: 220px;\n  background: var(--sidebar-bg);\n  display: flex;\n  flex-direction: column;\n  border-right: 1px solid var(--border);\n}\n\n.sidebar-brand {\n  padding: 20px 16px 4px;\n  font-size: 18px;\n  font-weight: 700;\n  color: var(--accent);\n  letter-spacing: 0.5px;\n}\n\n.sidebar-version {\n  padding: 0 16px 16px;\n  font-size: 11px;\n  color: var(--text-secondary);\n}\n\n.sidebar-nav {\n  flex: 1;\n  display: flex;\n  flex-direction: column;\n  padding: 8px 0;\n}\n\n.nav-item {\n  display: flex;\n  align-items: center;\n  gap: 10px;\n  padding: 10px 16px;\n  cursor: pointer;\n  color: var(--text-secondary);\n  text-decoration: none;\n  font-size: 13px;\n  border-left: 3px solid transparent;\n  transition: background 0.15s, color 0.15s, border-color 0.15s;\n  user-select: none;\n}\n\n.nav-item:hover {\n  background: var(--accent-glow);\n  color: var(--text-primary);\n}\n\n.nav-item.active {\n  background: var(--accent-glow);\n  color: var(--accent);\n  border-left-color: var(--accent);\n  font-weight: 600;\n}\n\n.nav-item .icon {\n  font-size: 16px;\n  width: 20px;\n  text-align: center;\n}\n\n.nav-item .badge {\n  margin-left: auto;\n  font-size: 11px;\n  background: var(--accent);\n  color: var(--bg);\n  border-radius: 8px;\n  padding: 1px 6px;\n  display: none;\n  font-weight: 700;\n}\n\n/* === Sidebar Footer === */\n.sidebar-footer {\n  padding: 12px 16px;\n  border-top: 1px solid var(--border);\n}\n\n.theme-toggle {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  cursor: pointer;\n  background: none;\n  border: 1px solid var(--border);\n  color: var(--text-secondary);\n  padding: 6px 10px;\n  border-radius: 4px;\n  font-family: var(--font-mono);\n  font-size: 12px;\n  width: 100%;\n  transition: background 0.15s, color 0.15s;\n  margin-bottom: 10px;\n}\n\n.theme-toggle:hover {\n  background: var(--accent-glow);\n  color: var(--text-primary);\n}\n\n.connection-status {\n  display: flex;\n  align-items: center;\n  gap: 8px;\n  font-size: 11px;\n  color: var(--text-secondary);\n}\n\n.status-dot {\n  width: 8px;\n  height: 8px;\n  border-radius: 50%;\n  background: #ef4444;\n  transition: background 0.3s;\n}\n\n.status-dot.connected {\n  background: #22c55e;\n}\n\n/* === Main Content === */\n.main {\n  flex: 1;\n  overflow-y: auto;\n  padding: 32px;\n  background: var(--bg);\n}\n\n.tab-panel {\n  display: none;\n}\n\n.tab-panel.active {\n  display: block;\n}\n\n.tab-panel h1 {\n  font-size: 22px;\n  font-weight: 700;\n  color: var(--accent);\n  margin-bottom: 16px;\n}\n\n.tab-panel .placeholder {\n  border: 1px dashed var(--border);\n  border-radius: 8px;\n  padding: 40px;\n  text-align: center;\n  color: var(--text-secondary);\n  font-size: 14px;\n}'


_JS = "/* === Tab Switching === */\ndocument.querySelectorAll('.nav-item').forEach(function(item) {\n  item.addEventListener('click', function() {\n    var tab = this.getAttribute('data-tab');\n    document.querySelectorAll('.nav-item').forEach(function(n) { n.classList.remove('active'); });\n    this.classList.add('active');\n    document.querySelectorAll('.tab-panel').forEach(function(p) { p.classList.remove('active'); });\n    var panel = document.getElementById('panel-' + tab);\n    if (panel) panel.classList.add('active');\n  });\n});\n\n/* === Theme Toggle === */\n(function() {\n  var html = document.documentElement;\n  var themeIcon = document.getElementById('themeIcon');\n  var themeLabel = document.getElementById('themeLabel');\n\n  function applyTheme(theme) {\n    if (theme === 'light') {\n      html.setAttribute('data-theme', 'light');\n      themeIcon.textContent = '\\u{1F319}';\n      themeLabel.textContent = 'Dark mode';\n    } else {\n      html.removeAttribute('data-theme');\n      themeIcon.textContent = '\\u{2600}\\u{FE0F}';\n      themeLabel.textContent = 'Light mode';\n    }\n  }\n\n  var saved = localStorage.getItem('gsd-dashboard-theme');\n  applyTheme(saved || 'dark');\n\n  document.getElementById('themeToggle').addEventListener('click', function() {\n    var current = html.getAttribute('data-theme');\n    var next = current === 'light' ? 'dark' : 'light';\n    localStorage.setItem('gsd-dashboard-theme', next);\n    applyTheme(next);\n  });\n})();\n\n/* === SSE Connection Status === */\n(function() {\n  var dot = document.getElementById('statusDot');\n  var text = document.getElementById('statusText');\n  var es;\n  var reconnectDelay = 2000;\n\n  function connect() {\n    es = new EventSource('/dashboard/events');\n\n    es.addEventListener('connected', function(e) {\n      dot.classList.add('connected');\n      text.textContent = 'Connected';\n    });\n\n    es.addEventListener('heartbeat', function(e) {\n      dot.classList.add('connected');\n      text.textContent = 'Connected';\n    });\n\n    es.onerror = function() {\n      dot.classList.remove('connected');\n      text.textContent = 'Disconnected';\n      es.close();\n      setTimeout(connect, reconnectDelay);\n    };\n  }\n\n  connect();\n})();"


def _build_dashboard_html() -> str:
    """Build the complete self-contained HTML dashboard page."""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        "<title>GSD Tandem Dashboard</title>\n"
        "<style>\n"
        + _CSS
        + "\n</style>\n"
        "</head>\n"
        "<body>\n"
        '<div class="app">\n'
        "\n"
        "  <!-- Sidebar -->\n"
        '  <aside class="sidebar">\n'
        '    <div class="sidebar-brand">GSD Tandem</div>\n'
        f'    <div class="sidebar-version">v{__version__}</div>\n'
        "\n"
        '    <nav class="sidebar-nav">\n'
        '      <div class="nav-item active" data-tab="overview">\n'
        f'        <span class="icon">{_ICON_OVERVIEW}</span>\n'
        "        <span>Overview</span>\n"
        '        <span class="badge"></span>\n'
        "      </div>\n"
        '      <div class="nav-item" data-tab="logs">\n'
        f'        <span class="icon">{_ICON_LOGS}</span>\n'
        "        <span>Logs</span>\n"
        '        <span class="badge"></span>\n'
        "      </div>\n"
        '      <div class="nav-item" data-tab="reviews">\n'
        f'        <span class="icon">{_ICON_REVIEWS}</span>\n'
        "        <span>Reviews</span>\n"
        '        <span class="badge"></span>\n'
        "      </div>\n"
        '      <div class="nav-item" data-tab="pool">\n'
        f'        <span class="icon">{_ICON_POOL}</span>\n'
        "        <span>Pool</span>\n"
        '        <span class="badge"></span>\n'
        "      </div>\n"
        "    </nav>\n"
        "\n"
        '    <div class="sidebar-footer">\n'
        '      <button class="theme-toggle" id="themeToggle">\n'
        f'        <span id="themeIcon">{_ICON_SUN}</span>\n'
        '        <span id="themeLabel">Light mode</span>\n'
        "      </button>\n"
        '      <div class="connection-status">\n'
        '        <span class="status-dot" id="statusDot"></span>\n'
        '        <span id="statusText">Disconnected</span>\n'
        "      </div>\n"
        "    </div>\n"
        "  </aside>\n"
        "\n"
        "  <!-- Main Content -->\n"
        '  <main class="main">\n'
        '    <div class="tab-panel active" id="panel-overview">\n'
        "      <h1>Overview</h1>\n"
        '      <div class="placeholder">Coming in Phase 9</div>\n'
        "    </div>\n"
        '    <div class="tab-panel" id="panel-logs">\n'
        "      <h1>Logs</h1>\n"
        '      <div class="placeholder">Coming in Phase 10</div>\n'
        "    </div>\n"
        '    <div class="tab-panel" id="panel-reviews">\n'
        "      <h1>Reviews</h1>\n"
        '      <div class="placeholder">Coming in Phase 11</div>\n'
        "    </div>\n"
        '    <div class="tab-panel" id="panel-pool">\n'
        "      <h1>Pool</h1>\n"
        '      <div class="placeholder">Coming in Phase 12</div>\n'
        "    </div>\n"
        "  </main>\n"
        "\n"
        "</div>\n"
        "\n"
        "<script>\n"
        + _JS
        + "\n</script>\n"
        "</body>\n"
        "</html>"
    )


async def _sse_event_generator() -> AsyncGenerator[str, None]:
    """Async generator yielding SSE events for the dashboard connection."""
    # Send initial connected event
    connected_data = json.dumps({
        "broker": "gsd-review-broker",
        "version": __version__,
    })
    yield f"event: connected\ndata: {connected_data}\n\n"

    # Send heartbeat every 15 seconds
    while True:
        try:
            await asyncio.sleep(15)
            heartbeat_data = json.dumps({
                "status": "connected",
                "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            })
            yield f"event: heartbeat\ndata: {heartbeat_data}\n\n"
        except asyncio.CancelledError:
            break


# Cache the rendered HTML so it is only built once per process.
_dashboard_html_cache: str | None = None


def _get_dashboard_html() -> str:
    """Return cached dashboard HTML, building on first call."""
    global _dashboard_html_cache
    if _dashboard_html_cache is None:
        _dashboard_html_cache = _build_dashboard_html()
    return _dashboard_html_cache


def register_dashboard_routes(mcp) -> None:
    """Register dashboard HTTP routes on the FastMCP server.

    Routes:
        GET /dashboard       - Main dashboard HTML page
        GET /dashboard/events - SSE endpoint for connection health
    """

    @mcp.custom_route("/dashboard", methods=["GET"])
    async def dashboard_page(request: Request) -> HTMLResponse:
        """Serve the dashboard HTML page."""
        return HTMLResponse(_get_dashboard_html())

    @mcp.custom_route("/dashboard/events", methods=["GET"])
    async def dashboard_events(request: Request) -> StreamingResponse:
        """SSE endpoint for dashboard connection health."""
        return StreamingResponse(
            _sse_event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
