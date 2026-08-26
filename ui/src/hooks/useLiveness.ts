import { useEffect, useRef } from 'react';
import { api } from '../api/client';

/**
 * Opens a liveness WebSocket to keep the server-side SSH connection alive
 * while the browser tab is open. The server sends {"type":"ping"} every 5s;
 * we respond with {"type":"pong"}. If the tab closes or the browser crashes,
 * the server detects the dead connection within ~15s and cleans up.
 *
 * Reconnects automatically if the WebSocket drops.
 */
export function useLiveness(host: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (!host) return;

    let closed = false;

    function connect() {
      const ws = new WebSocket(api.getLivenessWsUrl(host));
      wsRef.current = ws;

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'ping') {
            ws.send(JSON.stringify({ type: 'pong' }));
          }
        } catch {
          // Ignore malformed messages
        }
      };

      ws.onclose = () => {
        if (closed) return;
        // Reconnect after 3s
        reconnectTimer.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        // Let onclose handle reconnect
      };
    }

    connect();

    return () => {
      closed = true;
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) {
        wsRef.current.onclose = null;
        wsRef.current.close();
      }
    };
  }, [host]);
}
