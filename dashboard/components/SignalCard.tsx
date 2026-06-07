"use client";
import { Signal } from "@/lib/types";

export function SignalCard({ signal, isNew }: { signal: Signal; isNew?: boolean }) {
  const whale = signal.strategy === "whale";
  const accent = whale ? "var(--amber)" : "var(--green)";
  const time = new Date(signal.fired_at).toLocaleTimeString("en-GB");

  return (
    <div
      className={`rise ${isNew ? (whale ? "flash-amber" : "flash-green") : ""} relative border bg-[var(--panel)] p-3`}
      style={{ borderColor: "var(--border)", borderLeft: `2px solid ${accent}` }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-base font-bold font-display tracking-wide" style={{ color: accent }}>
            {signal.coin_symbol}
          </span>
          <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 border"
                style={{ color: accent, borderColor: accent, opacity: 0.9 }}>
            {whale ? "🐋 whale" : "signal"}
          </span>
        </div>
        <span className="text-[11px] text-[var(--faint)] tnum">{time}</span>
      </div>

      {whale ? (
        <p className="text-[12px] text-[var(--text)]/80 leading-snug">{signal.gemini_explanation}</p>
      ) : (
        <>
          <div className="flex gap-3 mb-1.5 text-[11px] tnum">
            <Score label="TOT" v={signal.total_score} accent={accent} bold />
            <Score label="TECH" v={signal.technical_score} accent="var(--cyan)" />
            <Score label="NEWS" v={signal.news_score} accent="var(--text)" />
          </div>
          <p className="text-[12px] text-[var(--muted)] leading-snug line-clamp-2">
            {signal.gemini_explanation}
          </p>
        </>
      )}
    </div>
  );
}

function Score({ label, v, accent, bold }: { label: string; v: number; accent: string; bold?: boolean }) {
  return (
    <span className="flex items-center gap-1">
      <span className="text-[9px] text-[var(--faint)] uppercase">{label}</span>
      <span className={bold ? "font-bold" : ""} style={{ color: accent }}>{v.toFixed(0)}</span>
    </span>
  );
}
