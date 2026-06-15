"use client";
import { useEffect, useState } from "react";
import { Position, LiveUpdate } from "@/lib/types";

const WHALE_HOLD_H = 12;
const STD_HOLD_H = 24;

function fmtPrice(p: number) {
  if (p <= 0) return "0";
  if (p >= 1) return p.toLocaleString("en-US", { maximumFractionDigits: 2 });
  // sub-dollar: show ~4 significant decimals so micro-priced coins (e.g. SATS at
  // ~0.0000003) render as 0.0000003, not scientific notation or $0.000000.
  const decimals = 3 - Math.floor(Math.log10(p));
  return p.toFixed(decimals).replace(/0+$/, "").replace(/\.$/, "");
}

export function PositionCard({ position, live }: { position: Position; live: LiveUpdate }) {
  const whale = position.strategy === "whale";
  const accent = whale ? "var(--amber)" : "var(--green)";
  // Prefer the live WS tick; fall back to the last-known price from /positions
  // (the WS stream is slow now), and only then to entry price.
  const u = live[position.id];
  const pnl = u?.pnl_pct ?? position.pnl_pct ?? 0;
  const current = u?.current_price ?? position.current_price ?? position.entry_price;
  const up = pnl >= 0;
  const pnlColor = up ? "var(--green)" : "var(--red)";

  const entryMs = new Date(position.entry_at).getTime();
  const holdH = whale ? WHALE_HOLD_H : STD_HOLD_H;
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const elapsed = (now - entryMs) / 1000;
  const remaining = Math.max(0, holdH * 3600 - elapsed);
  const remH = Math.floor(remaining / 3600);
  const remM = Math.floor((remaining % 3600) / 60);
  const timePct = Math.min(100, (elapsed / (holdH * 3600)) * 100);

  return (
    <div className="relative border bg-[var(--panel)] p-3"
         style={{ borderColor: "var(--border)", borderLeft: `2px solid ${accent}` }}>
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <span className="text-base font-bold font-display tracking-wide text-[var(--text)]">
            {position.coin_symbol}
          </span>
          {position.coin_name && position.coin_name.toLowerCase() !== position.coin_symbol.toLowerCase() && (
            <span className="text-[11px] text-[var(--muted)] truncate max-w-[140px]">{position.coin_name}</span>
          )}
          {whale && <span className="text-[11px]">🐋</span>}
          {position.scale_price != null && (
            <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 border"
                  style={{ color: "var(--green)", borderColor: "var(--green)" }}>
              ½ banked ${fmtPrice(position.scale_price)}
            </span>
          )}
        </div>
        <span className="text-lg font-bold tnum" style={{ color: pnlColor }}>
          {up ? "+" : ""}{pnl.toFixed(2)}%
        </span>
      </div>

      <div className="flex justify-between text-[11px] tnum text-[var(--muted)] mb-2">
        <span>entry ${fmtPrice(position.entry_price)}</span>
        <span style={{ color: "var(--text)" }}>now ${fmtPrice(current)}</span>
      </div>

      {/* time-to-timeout bar */}
      <div className="flex items-center gap-2">
        <div className="h-[3px] flex-1 bg-[var(--border)] overflow-hidden">
          <div className="h-full transition-all duration-1000"
               style={{ width: `${timePct}%`, background: accent, opacity: 0.7 }} />
        </div>
        <span className="text-[10px] text-[var(--faint)] tnum whitespace-nowrap">
          {remH}h {String(remM).padStart(2, "0")}m
        </span>
      </div>
    </div>
  );
}
