import { useEffect, useRef, useState } from "react";
import type { ControlState, PlaybackProgress, SocketMessage } from "./types";

const RECONNECT_MS = 1000;

/**
 * Subscribe to the control loop's state stream.
 *
 * Reconnects on its own, because the operator is typically standing next to the
 * arm rather than next to the browser: a dropped socket has to recover without
 * anyone pressing reload. `connected` is surfaced so the UI can say so out loud
 * rather than quietly showing a frozen pose as if it were live.
 */
export function useControlSocket() {
  const [state, setState] = useState<ControlState | null>(null);
  const [playback, setPlayback] = useState<PlaybackProgress | null>(null);
  const [connected, setConnected] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let disposed = false;

    const open = () => {
      if (disposed) return;

      const scheme = location.protocol === "https:" ? "wss" : "ws";
      socket = new WebSocket(`${scheme}://${location.host}/ws`);

      socket.onopen = () => setConnected(true);

      socket.onmessage = (event) => {
        const message: SocketMessage = JSON.parse(event.data);
        if (message.type === "state") {
          setState(message.data);
          setPlayback(message.data.playback);
        } else if (message.type === "playback") {
          setPlayback(message.data);
        }
      };

      socket.onclose = () => {
        setConnected(false);
        if (!disposed) timer.current = window.setTimeout(open, RECONNECT_MS);
      };

      socket.onerror = () => socket?.close();
    };

    open();
    return () => {
      disposed = true;
      window.clearTimeout(timer.current);
      socket?.close();
    };
  }, []);

  return { state, playback, connected };
}
