"use client";
import { Stats } from "@/lib/types";

function fmtCountdown(sec: number) {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function WinRate({ label, rate, closed, accent }: {
  label: string; rate: number; closed: number; accent: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.2em] text-[var(--muted)] font-display">
        {label}
      </span>
      <div className="flex items-baseline gap-2">
        <span className="text-2xl font-bold tnum" style={{ color: accent }}>
          {rate.toFixed(0)}<span className="text-sm text-[var(--muted)]">%</span>
        </span>
        <span className="text-[11px] text-[var(--faint)] tnum">{closed} closed</span>
      </div>
      <div className="h-[3px] w-full bg-[var(--border)] overflow-hidden">
        <div className="h-full transition-all duration-700"
             style={{ width: `${Math.min(100, rate)}%`, background: accent }} />
      </div>
    </div>
  );
}

export function StatBar({ stats, connected, nextScanIn }: {
  stats: Stats; connected: boolean; nextScanIn: number;
}) {
  return (
    <div className="border-b border-[var(--border)] bg-[var(--panel)]/60 backdrop-blur-sm">
      <div className="grid grid-cols-2 md:grid-cols-6 gap-x-6 gap-y-4 px-5 py-4">
        <WinRate label="Overall Win" rate={stats.overall.win_rate}
                 closed={stats.overall.total_closed} accent="var(--text)" />
        <WinRate label="Standard Win" rate={stats.standard.win_rate}
                 closed={stats.standard.total_closed} accent="var(--green)" />
        <WinRate label="Whale Win" rate={stats.whale.win_rate}
                 closed={stats.whale.total_closed} accent="var(--amber)" />

        <Metric label="Open" value={String(stats.overall.open_positions)}
                sub={`${stats.whale.open_positions}🐋`} />
        <Metric label="Signals Today" value={String(stats.overall.signals_today)}
                sub={`avg ${stats.overall.avg_pnl_pct >= 0 ? "+" : ""}${stats.overall.avg_pnl_pct}%`}
                subColor={stats.overall.avg_pnl_pct >= 0 ? "var(--green)" : "var(--red)"} />

        <div className="flex flex-col gap-1">
          <span className="text-[10px] uppercase tracking-[0.2em] text-[var(--muted)] font-display">
            Next Scan
          </span>
          <span className="text-2xl font-bold tnum text-[var(--cyan)]">{fmtCountdown(nextScanIn)}</span>
          <span className="flex items-center gap-1.5 text-[11px]"
                style={{ color: connected ? "var(--green)" : "var(--red)" }}>
            <span className="inline-block h-[7px] w-[7px] rounded-full pulse"
                  style={{ background: connected ? "var(--green)" : "var(--red)",
                           color: connected ? "var(--green)" : "var(--red)" }} />
            {connected ? "LIVE" : "OFFLINE"}
          </span>
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value, sub, subColor = "var(--faint)" }: {
  label: string; value: string; sub: string; subColor?: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-[0.2em] text-[var(--muted)] font-display">
        {label}
      </span>
      <span className="text-2xl font-bold tnum text-[var(--text)]">{value}</span>
      <span className="text-[11px] tnum" style={{ color: subColor }}>{sub}</span>
    </div>
  );
}
