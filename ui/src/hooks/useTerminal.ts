import { useEffect, useRef, useCallback } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { WebLinksAddon } from '@xterm/addon-web-links';
import '@xterm/xterm/css/xterm.css';

/**
 * Generic terminal session hook — connects xterm.js to a WebSocket URL.
 * Used by both Remote Terminal and Local Console.
 */
export function useTerminalSession(
  wsUrl: string,
  containerRef: React.RefObject<HTMLDivElement>,
  showConnectedMessage: boolean = true,
) {
  const termRef = useRef<Terminal | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pingIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const connect = useCallback(() => {
    if (!containerRef.current) return;

    // Clear any residual xterm DOM from a previous session
    while (containerRef.current.firstChild) {
      containerRef.current.removeChild(containerRef.current.firstChild);
    }

    const term = new Terminal({
      scrollback: 10000,
      fontSize: 15,
      fontFamily: 'Cascadia Mono, monospace',
      fontWeight: 'normal',
      fontWeightBold: 'normal',
      cursorBlink: true,
      theme: {
        foreground: '#26FF0E',
        background: '#000000',
        selectionBackground: '#FFFFFF',
      },
    });
    const fitAddon = new FitAddon();
    term.loadAddon(fitAddon);
    term.loadAddon(new WebLinksAddon());
    term.open(containerRef.current);
    fitAddon.fit();
    termRef.current = term;

    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      if (showConnectedMessage) {
        term.write('\r\n\x1b[32m[Connected]\x1b[0m\r\n');
      }
      ws.send(JSON.stringify({ type: 'resize', cols: term.cols, rows: term.rows }));
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'output') {
          term.write(msg.data);
        } else if (msg.type === 'ready') {
          // Terminal ready
        } else if (msg.type === 'exited') {
          term.write(`\r\n\x1b[33m[Process exited with code ${msg.exit_code}]\x1b[0m\r\n`);
        } else if (msg.type === 'error') {
          term.write(`\r\n\x1b[31m[Error: ${msg.message}]\x1b[0m\r\n`);
        } else if (msg.type === 'pong') {
          // Heartbeat ok
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      term.write('\r\n\x1b[31m[Disconnected]\x1b[0m\r\n');
    };

    ws.onerror = () => {
      term.write('\r\n\x1b[31m[WebSocket error]\x1b[0m\r\n');
    };

    term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });

    term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    // Heartbeat
    pingIntervalRef.current = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 30000);

    // Handle window resize
    const resizeHandler = () => fitAddon.fit();
    window.addEventListener('resize', resizeHandler);
  }, [wsUrl, containerRef]);

  const disconnect = useCallback(() => {
    if (pingIntervalRef.current) {
      clearInterval(pingIntervalRef.current);
      pingIntervalRef.current = null;
    }
    if (wsRef.current) {
      wsRef.current.close();
      wsRef.current = null;
    }
    if (termRef.current) {
      termRef.current.dispose();
      termRef.current = null;
    }
  }, []);

  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  const reconnect = useCallback(() => {
    disconnect();
    // Small delay to allow cleanup before reconnecting
    setTimeout(() => connect(), 100);
  }, [connect, disconnect]);

  return { connect, disconnect, reconnect, termRef, wsRef };
}
