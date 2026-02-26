"""Tests for the dashboard HTTP routes and content generation."""

from __future__ import annotations

import asyncio
import re
from unittest.mock import patch

import pytest

from gsd_review_broker.dashboard import _build_dashboard_html, _sse_event_generator


# ---- HTML Content Tests ----


class TestDashboardHTML:
    """Tests for the generated dashboard HTML content."""

    def setup_method(self) -> None:
        """Build HTML once for all tests in this class."""
        self.html = _build_dashboard_html()

    def test_dashboard_html_contains_branding(self) -> None:
        """The HTML output contains GSD Tandem text."""
        assert "GSD Tandem" in self.html

    def test_dashboard_html_contains_nav_sections(self) -> None:
        """HTML contains all four nav items: Overview, Logs, Reviews, Pool."""
        for section in ("Overview", "Logs", "Reviews", "Pool"):
            assert section in self.html, f"Missing nav section: {section}"

    def test_dashboard_html_no_external_urls(self) -> None:
        """HTML does not reference external URLs (self-containment, DASH-01).

        Allow http://127.0.0.1 and http://localhost since those are local
        SSE endpoint references, not external CDN dependencies.
        """
        # Check for external URL patterns in src, href, or url() attributes
        # Use string search instead of regex for simplicity
        for attr_prefix in ["src=", "href=", "url("]:
            idx = 0
            while True:
                idx = self.html.find(attr_prefix, idx)
                if idx == -1:
                    break
                # Extract the URL portion after the attribute
                rest = self.html[idx + len(attr_prefix):idx + len(attr_prefix) + 200]
                # Strip leading quotes
                rest = rest.lstrip("'\"")
                if rest.startswith("http://") or rest.startswith("https://"):
                    # Allow local references
                    assert rest.startswith("http://127.0.0.1") or rest.startswith("http://localhost"), (
                        f"External URL found near position {idx}: {rest[:80]}"
                    )
                idx += 1

    def test_dashboard_html_contains_theme_toggle(self) -> None:
        """HTML contains theme toggle mechanism (data-theme, localStorage)."""
        assert "data-theme" in self.html or "data_theme" in self.html
        assert "localStorage" in self.html

    def test_dashboard_html_contains_css_variables(self) -> None:
        """HTML contains CSS custom property definitions."""
        assert "--bg" in self.html
        assert "--accent" in self.html
        assert "--text-primary" in self.html

    def test_dashboard_html_contains_sse_setup(self) -> None:
        """HTML contains EventSource and /dashboard/events reference."""
        assert "EventSource" in self.html
        assert "/dashboard/events" in self.html

    def test_dashboard_html_contains_monospace_font(self) -> None:
        """HTML contains monospace font-family declaration."""
        assert "monospace" in self.html


# ---- SSE Generator Tests ----


class TestSSEGenerator:
    """Tests for the SSE async event generator."""

    async def test_sse_generator_yields_connected_event(self) -> None:
        """The SSE generator yields a connected event as its first message."""
        gen = _sse_event_generator()
        first_event = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "event: connected" in first_event
        assert "gsd-review-broker" in first_event
        await gen.aclose()

    async def test_sse_generator_yields_heartbeat(self) -> None:
        """After connected, the generator yields heartbeat events."""
        gen = _sse_event_generator()
        # Consume the connected event first
        await asyncio.wait_for(gen.__anext__(), timeout=2.0)

        # Mock asyncio.sleep to return immediately so we do not wait 15s
        with patch("gsd_review_broker.dashboard.asyncio.sleep", return_value=None):
            heartbeat_event = await asyncio.wait_for(gen.__anext__(), timeout=2.0)
        assert "event: heartbeat" in heartbeat_event
        assert "timestamp" in heartbeat_event
        await gen.aclose()
