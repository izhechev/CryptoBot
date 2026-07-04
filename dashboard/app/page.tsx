"use client";
import { useEffect, useState, useRef, useCallback } from "react";
import { StatBar } from "@/components/StatBar";
import { PositionCard } from "@/components/PositionCard";
import { TradesTable } from "@/components/TradesTable";
import { ConfigDrawer } from "@/components/ConfigDrawer";
import { useCryptoBotWs } from "@/lib/useWebSocket";
import { Position, PendingOrder, Stats, BotConfig, LiveUpdate } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS = API.replace(/^http/, "ws") + "/ws";

const EMPTY_STRAT = { total_closed: 0, wins: 0, losses: 0, win_rate: 0, open_positions: 0, signals_today: 0, avg_pnl_pct: 0 };
const EMPTY_STATS: Stats = { overall: EMPTY_STRAT, standard: EMPTY_STRAT, whale: EMPTY_STRAT, next_scan_in: null, regime_bullish: null, whales_blocked: 0 };

export default function Dashboard() {
  const [positions, setPositions] = useState<Position[]>([]);
  const [pending, setPending] = useState<PendingOrder[]>([]);
  const [stats, setStats] = useState<Stats>(EMPTY_STATS);
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [live, setLive] = useState<LiveUpdate>({});
  const [nextScanIn, setNextScanIn] = useState(0);

  const { messages, connected } = useCryptoBotWs(WS);

  const refresh = useCallback(async () => {
    try {
      const [p, pend, st, cfg] = await Promise.all([
        fetch(`${API}/positions`).then((r) => r.json()),
        fetch(`${API}/pending`).then((r) => r.json()),
        fetch(`${API}/stats`).then((r) => r.json()),
        fetch(`${API}/config`).then((r) => r.json()),
      ]);
      setPositions(p); setPending(pend); setStats(st); setConfig(cfg);
      // Anchor the countdown to the backend's clock (re-syncs every poll + on reload).
      if (typeof st.next_scan_in === "number") setNextScanIn(st.next_scan_in);
    } catch {
      /* backend not up yet — retry on next tick */
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Working limits arm/expire server-side without a WS event — poll lightly.
  useEffect(() => {
    const t = setInterval(refresh, 15000);
    return () => clearInterval(t);
  }, [refresh]);

  // Tick down between polls; floor at 0 (a scan is due) and let refresh re-anchor.
  useEffect(() => {
    const t = setInterval(() => {
      setNextScanIn((prev) => (prev <= 0 ? 0 : prev - 1));
    }, 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const latest = messages[0];
    if (!latest) return;
    if (latest.type === "signal_fired") {
      refresh();  // re-anchors nextScanIn from the backend clock
    } else if (latest.type === "position_closed") {
      refresh();
    } else if (latest.type === "position_updated") {
      setLive((prev) => ({ ...prev, [latest.id]: { current_price: latest.current_price, pnl_pct: latest.pnl_pct } }));
    } else if (latest.type === "prices") {
      // Batched feed: apply every position's price in one render so they all move together.
      setLive((prev) => {
        const next = { ...prev };
        for (const u of latest.updates) next[u.id] = { current_price: u.current_price, pnl_pct: u.pnl_pct };
        return next;
      });
    }
  }, [messages, refresh]);

  const openPositions = positions.filter((p) => p.outcome === null);
  const closedPositions = positions.filter((p) => p.outcome !== null);
  const openWhales = openPositions.filter((p) => p.strategy === "whale");
  const openStd = openPositions.filter((p) => p.strategy !== "whale");

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="flex items-center justify-between px-5 py-3 border-b border-[var(--border)] bg-[var(--panel)]/40">
        <div className="flex items-center gap-3">
          <span className="text-xl">🛰️</span>
          <div className="leading-none">
            <h1 className="font-display font-extrabold tracking-[0.05em] text-[var(--text)]">
              CRYPTO<span className="text-[var(--green)]">BOT</span>
              <span className="text-[var(--amber)]">.</span>
            </h1>
            <span className="text-[10px] uppercase tracking-[0.3em] text-[var(--faint)]">signal terminal</span>
          </div>
        </div>
        {config && <ConfigDrawer config={config} />}
      </header>

      <StatBar stats={stats} connected={connected} nextScanIn={nextScanIn} />

      {/* Working retest limits (armed by the scanner, filled/expired by the tracker) */}
      {pending.length > 0 && (
        <section className="px-3 pt-3">
          <PanelHeader label="Working Limits" count={pending.length} accent="var(--amber)" inline />
          <div className="mt-2 flex flex-wrap gap-2">
            {pending.map((o) => (
              <div key={o.id}
                   className="border border-dashed bg-[var(--panel)] px-3 py-2 text-[11px] tnum"
                   style={{ borderColor: "var(--amber)" }}>
                <span className="font-display font-bold text-[var(--text)]">{o.coin_symbol}</span>
                {o.coin_name && o.coin_name.toLowerCase() !== o.coin_symbol.toLowerCase() && (
                  <span className="text-[var(--muted)]"> {o.coin_name}</span>
                )}
                <span className="text-[var(--amber)]"> 🐋 limit ${o.limit_price}</span>
                <span className="text-[var(--faint)]"> · vol {o.volume_ratio.toFixed(1)}x · waits for pullback</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Open positions */}
      <section className="flex flex-col flex-1 min-h-0">
        <PanelHeader label="Open Positions" count={openPositions.length} accent="var(--amber)" />
        <div className="overflow-y-auto p-3 flex flex-col gap-2 max-h-[62vh]">
          {openWhales.length > 0 && <GroupLabel text="🐋 Whale rides" accent="var(--amber)" />}
          {openWhales.map((p) => <PositionCard key={p.id} position={p} live={live} />)}
          {openStd.length > 0 && openWhales.length > 0 && (
            <GroupLabel text="Standard" accent="var(--green)" />
          )}
          {openStd.map((p) => <PositionCard key={p.id} position={p} live={live} />)}
          {openPositions.length === 0 && <Empty text="No open positions." />}
        </div>
      </section>

      {/* Closed trades */}
      <section className="p-3 border-t border-[var(--border)]">
        <PanelHeader label="Closed Trades" count={closedPositions.length} accent="var(--text)" inline />
        <div className="mt-2"><TradesTable positions={closedPositions} /></div>
      </section>
    </div>
  );
}

function PanelHeader({ label, count, accent, inline }: {
  label: string; count: number; accent: string; inline?: boolean;
}) {
  return (
    <div className={`flex items-center gap-2 px-4 py-2.5 ${inline ? "" : "border-b border-[var(--border)] bg-[var(--panel)]/30"}`}>
      <span className="inline-block h-[6px] w-[6px] rounded-full" style={{ background: accent }} />
      <h2 className="text-[11px] uppercase tracking-[0.2em] font-display font-semibold text-[var(--text)]">{label}</h2>
      <span className="text-[11px] tnum text-[var(--faint)]">[{count}]</span>
    </div>
  );
}

function GroupLabel({ text, accent }: { text: string; accent: string }) {
  return (
    <div className="text-[10px] uppercase tracking-[0.2em] font-display pt-1 pb-0.5" style={{ color: accent }}>
      {text}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-[12px] text-[var(--faint)] px-1 py-6 text-center">{text}</p>;
}
