"use client";
import { useEffect, useRef, useState, useCallback } from "react";

export type WsMessage =
  | { type: "signal_fired"; strategy: string; coin: string; score: number; explanation: string; entry_price: number }
  | { type: "position_updated"; id: number; strategy: string; coin: string; current_price: number; pnl_pct: number }
  | { type: "position_closed"; strategy: string; coin: string; outcome: string; pnl_pct: number }
  | { type: "scan_started" }
  | { type: "scan_completed" };

export function useCryptoBotWs(url: string) {
  const [messages, setMessages] = useState<WsMessage[]>([]);
  const [connected, setConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  const connect = useCallback(() => {
    const socket = new WebSocket(url);
    ws.current = socket;
    socket.onopen = () => setConnected(true);
    socket.onclose = () => {
      setConnected(false);
      setTimeout(connect, 3000); // auto-reconnect
    };
    socket.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data) as WsMessage;
        setMessages((prev) => [msg, ...prev].slice(0, 200));
      } catch {
        /* ignore malformed frames */
      }
    };
  }, [url]);

  useEffect(() => {
    connect();
    return () => ws.current?.close();
  }, [connect]);

  return { messages, connected };
}
