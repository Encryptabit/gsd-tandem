/**
 * Theme initialization and toggle logic.
 * Manages dark/light theme via data-theme attribute on <html>.
 * Persists preference to localStorage.
 */

const STORAGE_KEY = 'gsd-dashboard-theme';
const DEFAULT_THEME = 'dark';

function getStoredTheme(): string {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') {
      return stored;
    }
  } catch {
    // localStorage unavailable (e.g., private browsing)
  }
  return DEFAULT_THEME;
}

function applyTheme(theme: string): void {
  document.documentElement.setAttribute('data-theme', theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Ignore storage errors
  }
}

function toggleTheme(): void {
  const current = document.documentElement.getAttribute('data-theme') || DEFAULT_THEME;
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
}

// Apply saved theme on script load
applyTheme(getStoredTheme());

// Wire up the toggle button
document.addEventListener('DOMContentLoaded', () => {
  const btn = document.getElementById('theme-toggle');
  if (btn) {
    btn.addEventListener('click', toggleTheme);
  }
});
