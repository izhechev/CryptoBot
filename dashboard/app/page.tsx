"use client";
import { useEffect, useState, useRef, useCallback } from "react";
import { StatBar } from "@/components/StatBar";
import { SignalCard } from "@/components/SignalCard";
import { PositionCard } from "@/components/PositionCard";
import { TradesTable } from "@/components/TradesTable";
import { ConfigDrawer } from "@/components/ConfigDrawer";
import { useCryptoBotWs } from "@/lib/useWebSocket";
import { Signal, Position, Stats, BotConfig, LiveUpdate } from "@/lib/types";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
const WS = API.replace(/^http/, "ws") + "/ws";

const EMPTY_STRAT = { total_closed: 0, wins: 0, losses: 0, win_rate: 0, open_positions: 0, signals_today: 0, avg_pnl_pct: 0 };
const EMPTY_STATS: Stats = { overall: EMPTY_STRAT, standard: EMPTY_STRAT, whale: EMPTY_STRAT };

export default function Dashboard() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [positions, setPositions] = useState<Position[]>([]);
  const [stats, setStats] = useState<Stats>(EMPTY_STATS);
  const [config, setConfig] = useState<BotConfig | null>(null);
  const [live, setLive] = useState<LiveUpdate>({});
  const [nextScanIn, setNextScanIn] = useState(0);
  const [newest, setNewest] = useState<number | null>(null);
  const scanInterval = useRef(30 * 60);

  const { messages, connected } = useCryptoBotWs(WS);

  const refresh = useCallback(async () => {
    try {
      const [s, p, st, cfg] = await Promise.all([
        fetch(`${API}/signals`).then((r) => r.json()),
        fetch(`${API}/positions`).then((r) => r.json()),
        fetch(`${API}/stats`).then((r) => r.json()),
        fetch(`${API}/config`).then((r) => r.json()),
      ]);
      setSignals(s); setPositions(p); setStats(st); setConfig(cfg);
      scanInterval.current = cfg.scan_interval_minutes * 60;
    } catch {
      /* backend not up yet — retry on next tick */
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  useEffect(() => {
    const t = setInterval(() => {
      setNextScanIn((prev) => (prev <= 1 ? scanInterval.current : prev - 1));
    }, 1000);
    return () => clearInterval(t);
  }, []);

  useEffect(() => {
    const latest = messages[0];
    if (!latest) return;
    if (latest.type === "signal_fired") {
      refresh();
      setNewest(Date.now());
      setNextScanIn(scanInterval.current);
    } else if (latest.type === "position_closed") {
      refresh();
    } else if (latest.type === "position_updated") {
      setLive((prev) => ({ ...prev, [latest.id]: { current_price: latest.current_price, pnl_pct: latest.pnl_pct } }));
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

      {/* Main grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 flex-1 min-h-0">
        {/* Signals */}
        <section className="border-r border-[var(--border)] flex flex-col min-h-0">
          <PanelHeader label="Live Signals" count={signals.length} accent="var(--green)" />
          <div className="overflow-y-auto p-3 flex flex-col gap-2 max-h-[62vh]">
            {signals.map((s, i) => (
              <SignalCard key={s.id} signal={s} isNew={i === 0 && newest !== null} />
            ))}
            {signals.length === 0 && <Empty text="Scanning… no signals fired yet." />}
          </div>
        </section>

        {/* Positions */}
        <section className="flex flex-col min-h-0">
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
      </div>

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
