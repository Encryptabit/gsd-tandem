/**
 * Shared EventSource connection utility for SSE.
 * Creates and manages a single EventSource connection to /dashboard/events.
 * Auto-reconnects with exponential backoff.
 * Dispatches sse-status custom events for the ConnectionStatus component.
 */

type SSECallback = (data: unknown) => void;

interface SSEManager {
  subscribe: (eventType: string, callback: SSECallback) => void;
  unsubscribe: (eventType: string, callback: SSECallback) => void;
  destroy: () => void;
}

function createSSEManager(): SSEManager {
  const subscribers = new Map<string, Set<SSECallback>>();
  let eventSource: EventSource | null = null;
  let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
  let reconnectDelay = 1000;
  let destroyed = false;

  const MAX_RECONNECT_DELAY = 30000;
  const SSE_URL = '/dashboard/events';

  function dispatchStatus(status: string): void {
    window.dispatchEvent(
      new CustomEvent('sse-status', { detail: { status } })
    );
  }

  function handleMessage(event: MessageEvent): void {
    try {
      const data = JSON.parse(event.data);
      const eventType = data.type || 'message';
      const callbacks = subscribers.get(eventType);
      if (callbacks) {
        callbacks.forEach(cb => {
          try {
            cb(data);
          } catch (err) {
            console.error('[SSE] Callback error:', err);
          }
        });
      }

      // Also notify a wildcard '*' subscriber
      const wildcard = subscribers.get('*');
      if (wildcard) {
        wildcard.forEach(cb => {
          try {
            cb(data);
          } catch (err) {
            console.error('[SSE] Wildcard callback error:', err);
          }
        });
      }
    } catch {
      // Non-JSON message, ignore
    }
  }

  function connect(): void {
    if (destroyed) return;

    try {
      eventSource = new EventSource(SSE_URL);

      eventSource.onopen = () => {
        reconnectDelay = 1000; // Reset backoff on success
        dispatchStatus('connected');
      };

      eventSource.onmessage = handleMessage;

      eventSource.onerror = () => {
        if (destroyed) return;
        eventSource?.close();
        eventSource = null;
        dispatchStatus('disconnected');
        scheduleReconnect();
      };
    } catch {
      // EventSource constructor failed (e.g., invalid URL in some envs)
      dispatchStatus('disconnected');
      scheduleReconnect();
    }
  }

  function scheduleReconnect(): void {
    if (destroyed) return;
    dispatchStatus('reconnecting');
    reconnectTimeout = setTimeout(() => {
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
      connect();
    }, reconnectDelay);
  }

  function subscribe(eventType: string, callback: SSECallback): void {
    if (!subscribers.has(eventType)) {
      subscribers.set(eventType, new Set());
    }
    subscribers.get(eventType)!.add(callback);
  }

  function unsubscribe(eventType: string, callback: SSECallback): void {
    subscribers.get(eventType)?.delete(callback);
  }

  function destroy(): void {
    destroyed = true;
    if (reconnectTimeout) clearTimeout(reconnectTimeout);
    eventSource?.close();
    eventSource = null;
    subscribers.clear();
  }

  // Start connection
  connect();

  return { subscribe, unsubscribe, destroy };
}

// Global singleton
const sseManager = createSSEManager();

// Expose for other scripts
declare global {
  interface Window {
    gsdSSE: SSEManager;
  }
}
window.gsdSSE = sseManager;

/**
 * Convenience: subscribe to a specific SSE event type.
 * Usage from other scripts: window.gsdSSE.subscribe('review_update', (data) => { ... });
 */
export function onSSEMessage(eventType: string, callback: SSECallback): void {
  sseManager.subscribe(eventType, callback);
}
