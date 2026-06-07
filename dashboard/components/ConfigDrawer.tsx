"use client";
import { useState } from "react";
import { BotConfig } from "@/lib/types";

export function ConfigDrawer({ config }: { config: BotConfig }) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <button onClick={() => setOpen(true)}
              className="text-[11px] uppercase tracking-[0.15em] font-display text-[var(--muted)] hover:text-[var(--text)] border border-[var(--border)] hover:border-[var(--border-bright)] px-3 py-1.5 transition-colors">
        ⚙ Config
      </button>
      {open && (
        <div className="fixed inset-0 z-50 flex justify-end bg-black/70 backdrop-blur-sm"
             onClick={() => setOpen(false)}>
          <div className="h-full w-[340px] bg-[var(--panel)] border-l border-[var(--border-bright)] p-6 overflow-y-auto"
               onClick={(e) => e.stopPropagation()}>
            <div className="flex justify-between items-center mb-6">
              <h2 className="font-display font-bold uppercase tracking-[0.15em] text-sm text-[var(--text)]">
                Configuration
              </h2>
              <button onClick={() => setOpen(false)} className="text-[var(--muted)] hover:text-[var(--text)]">✕</button>
            </div>

            <Section title="Standard Strategy" accent="var(--green)">
              <Row k="Signal threshold" v={`${config.signal_threshold}`} />
              <Row k="Take profit" v={`+${config.take_profit_pct}%`} />
              <Row k="Stop loss" v={`−${config.stop_loss_pct}%`} />
              <Row k="Max hold" v={`${config.max_hold_hours}h`} />
            </Section>

            <Section title="Whale Strategy" accent="var(--amber)">
              <Row k="Enabled" v={config.whale_enabled ? "yes" : "no"} />
              <Row k="Take profit" v={`+${config.whale_take_profit_pct}%`} />
              <Row k="Stop loss" v={`−${config.whale_stop_loss_pct}%`} />
              <Row k="Max hold" v={`${config.whale_max_hold_hours}h`} />
            </Section>

            <Section title="Engine" accent="var(--cyan)">
              <Row k="Scan interval" v={`${config.scan_interval_minutes}m`} />
              <Row k="Track interval" v={`${config.tracking_interval_seconds}s`} />
            </Section>

            <p className="text-[10px] text-[var(--faint)] mt-6 leading-relaxed">
              Edit <span className="text-[var(--muted)]">backend/config.yaml</span> and restart the bot to change these.
            </p>
          </div>
        </div>
      )}
    </>
  );
}

function Section({ title, accent, children }: { title: string; accent: string; children: React.ReactNode }) {
  return (
    <div className="mb-6">
      <h3 className="text-[10px] uppercase tracking-[0.2em] font-display mb-2 pb-1 border-b"
          style={{ color: accent, borderColor: "var(--border)" }}>{title}</h3>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between text-[12px]">
      <span className="text-[var(--muted)]">{k}</span>
      <span className="tnum text-[var(--text)]">{v}</span>
    </div>
  );
}
