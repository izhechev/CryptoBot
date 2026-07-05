"use client";
import { Position } from "@/lib/types";

function fmtPrice(p: number | null) {
  if (p == null) return "—";
  if (p >= 1) return p.toLocaleString("en-US", { maximumFractionDigits: 2 });
  return p.toPrecision(4);
}

const OUTCOME_STYLE: Record<string, { fg: string; bg: string }> = {
  win: { fg: "var(--green)", bg: "rgba(61,220,132,0.1)" },
  loss: { fg: "var(--red)", bg: "rgba(255,91,91,0.1)" },
  timeout: { fg: "var(--amber)", bg: "rgba(255,182,39,0.1)" },
  dead: { fg: "var(--muted)", bg: "rgba(148,163,184,0.1)" },
};

export function TradesTable({ positions }: { positions: Position[] }) {
  return (
    <div className="overflow-x-auto border border-[var(--border)]">
      <table className="w-full text-[12px] tnum">
        <thead>
          <tr className="text-[10px] uppercase tracking-[0.15em] text-[var(--muted)] font-display bg-[var(--panel-2)]">
            {["Coin", "Strat", "Entry", "Exit", "P&L", "Outcome", "Held"].map((h) => (
              <th key={h} className="px-3 py-2 text-left font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {positions.map((p) => {
            const whale = p.strategy === "whale";
            const held = p.exit_at
              ? Math.round((new Date(p.exit_at).getTime() - new Date(p.entry_at).getTime()) / 60000)
              : null;
            const o = OUTCOME_STYLE[p.outcome ?? ""] ?? { fg: "var(--muted)", bg: "transparent" };
            const pnlUp = (p.pnl_pct ?? 0) >= 0;
            return (
              <tr key={p.id} className="border-t border-[var(--border)] hover:bg-[var(--panel-2)]/60">
                <td className="px-3 py-2 font-bold font-display"
                    style={{ color: whale ? "var(--amber)" : "var(--text)" }}>
                  {p.coin_symbol}
                </td>
                <td className="px-3 py-2 text-[var(--muted)]">{whale ? "🐋" : "·"}</td>
                <td className="px-3 py-2 text-[var(--muted)]">${fmtPrice(p.entry_price)}</td>
                <td className="px-3 py-2 text-[var(--muted)]">${fmtPrice(p.exit_price)}</td>
                <td className="px-3 py-2 font-bold" style={{ color: pnlUp ? "var(--green)" : "var(--red)" }}>
                  {p.pnl_pct != null ? `${pnlUp ? "+" : ""}${p.pnl_pct.toFixed(2)}%` : "—"}
                </td>
                <td className="px-3 py-2">
                  <span className="px-2 py-0.5 text-[10px] uppercase tracking-wider"
                        style={{ color: o.fg, background: o.bg }}>
                    {p.outcome ?? "open"}
                  </span>
                </td>
                <td className="px-3 py-2 text-[var(--faint)]">{held != null ? `${held}m` : "—"}</td>
              </tr>
            );
          })}
          {positions.length === 0 && (
            <tr><td colSpan={7} className="px-3 py-8 text-center text-[var(--faint)]">
              No closed trades yet — waiting for the first exit.
            </td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
