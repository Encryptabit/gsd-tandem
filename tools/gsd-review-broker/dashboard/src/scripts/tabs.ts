/**
 * Tab switching logic for sidebar navigation.
 * Uses data-tab attributes on nav items and id="tab-{name}" on panels.
 * Persists active tab to localStorage.
 */

const TAB_STORAGE_KEY = 'gsd-dashboard-active-tab';
const DEFAULT_TAB = 'overview';

function getStoredTab(): string {
  try {
    const stored = localStorage.getItem(TAB_STORAGE_KEY);
    if (stored) return stored;
  } catch {
    // localStorage unavailable
  }
  return DEFAULT_TAB;
}

function activateTab(tabName: string): void {
  // Hide all panels
  const panels = document.querySelectorAll<HTMLElement>('.tab-panel');
  panels.forEach(panel => {
    panel.style.display = 'none';
  });

  // Show target panel
  const target = document.getElementById(`tab-${tabName}`);
  if (target) {
    target.style.display = '';
  }

  // Update nav item active states
  const navItems = document.querySelectorAll<HTMLElement>('.nav-item');
  navItems.forEach(item => {
    if (item.getAttribute('data-tab') === tabName) {
      item.classList.add('active');
    } else {
      item.classList.remove('active');
    }
  });

  // Persist
  try {
    localStorage.setItem(TAB_STORAGE_KEY, tabName);
  } catch {
    // Ignore storage errors
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const nav = document.getElementById('sidebar-nav');
  if (!nav) return;

  // Event delegation for nav clicks
  nav.addEventListener('click', (event: Event) => {
    const target = (event.target as HTMLElement).closest<HTMLElement>('.nav-item');
    if (!target) return;

    const tabName = target.getAttribute('data-tab');
    if (tabName) {
      activateTab(tabName);
    }
  });

  // Restore last active tab
  activateTab(getStoredTab());
});
