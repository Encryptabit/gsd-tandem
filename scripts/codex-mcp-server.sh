#!/usr/bin/env bash
# Platform-aware wrapper for codex mcp-server.
# Works on native Linux and when invoked from Windows via WSL.
exec codex mcp-server "$@"
