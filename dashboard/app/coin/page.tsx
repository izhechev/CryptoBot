"use client";
import { useState } from "react";
import Link from "next/link";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Gate = { name: string; passed: boolean; detail: string };
type Lane = {
  lane: string;
  would_open: boolean;
  blocked_by: string | null;
  reason: string;
  checks: Gate[];
};
type Report = {
  symbol: string;
  name: string;
  found: boolean;
  price: number | null;
  volume_24h: number | null;
  regime_bullish: boolean;
  regime_detail: string;
  in_universe: boolean;
  universe_note: string;
  readings: Record<string, number | boolean | null>;
  whale: { volume_ratio: number; thrust_pct: number; as_of: string } | null;
  spot: Lane | null;
  whale_lane: Lane | null;
  error: string;
};

function fmtPrice(p: number | null) {
  if (p == null) return "—";
  if (p >= 1) return `$${p.toLocaleString(undefined, { maximumFractionDigits: 4 })}`;
  return `$${p.toPrecision(4)}`;
}

function num(v: unknown, digits = 1) {
  return typeof v === "number" ? v.toFixed(digits) : "—";
}

export default function CoinPage() {
  const [symbol, setSymbol] = useState("");
  const [news, setNews] = useState(false);
  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");

  async function inspect(e?: React.FormEvent) {
    e?.preventDefault();
    const s = symbol.trim();
    if (!s) return;
    setLoading(true);
    setErr("");
    try {
      const r = await fetch(`${API}/coin/${encodeURIComponent(s)}?news=${news}`);
      setReport(await r.json());
    } catch {
      setErr("could not reach the bot API — is backend.main running?");
      setReport(null);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="min-h-screen p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h1 className="font-display font-bold uppercase tracking-[0.15em] text-sm text-[var(--text)]">
          Coin Inspector
        </h1>
        <Link href="/" className="text-[11px] uppercase tracking-[0.15em] font-display text-[var(--muted)] hover:text-[var(--text)] border border-[var(--border)] hover:border-[var(--border-bright)] px-3 py-1.5 transition-colors">
          ← Dashboard
        </Link>
      </div>

      <form onSubmit={inspect} className="flex gap-2 items-center mb-6">
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value.toUpperCase())}
          placeholder="SOL"
          className="bg-[var(--panel)] border border-[var(--border)] focus:border-[var(--border-bright)] outline-none px-3 py-2 text-[13px] tnum text-[var(--text)] w-40"
        />
        <button
          type="submit"
          disabled={loading}
          className="text-[11px] uppercase tracking-[0.15em] font-display text-[var(--muted)] hover:text-[var(--text)] border border-[var(--border)] hover:border-[var(--border-bright)] px-3 py-2 transition-colors disabled:opacity-40"
        >
          {loading ? "checking…" : "Inspect"}
        </button>
        <label className="text-[11px] text-[var(--muted)] flex items-center gap-1.5 ml-2">
          <input type="checkbox" checked={news} onChange={(e) => setNews(e.target.checked)} />
          run news gate
        </label>
      </form>

      {err && <p className="text-[12px] text-[var(--red)]">{err}</p>}

      {report && (
        <div className="flex flex-col gap-6">
          <div>
            <div className="flex items-baseline gap-3">
              <span className="font-display font-bold text-lg text-[var(--text)]">{report.symbol}</span>
              <span className="text-[13px] text-[var(--muted)]">{report.name}</span>
            </div>
            {report.found && (
              <div className="text-[12px] tnum text-[var(--muted)] mt-1">
                {fmtPrice(report.price)}
                {report.volume_24h != null && ` · $${(report.volume_24h / 1e6).toFixed(1)}M/24h`}
              </div>
            )}
            {report.error && <p className="text-[12px] text-[var(--amber)] mt-2">{report.error}</p>}
            {report.universe_note && (
              <p className="text-[12px] text-[var(--amber)] mt-2">⚠ {report.universe_note}</p>
            )}
          </div>

          {report.found && (
            <Section title="Regime" accent="var(--cyan)">
              <Row k={report.regime_bullish ? "BULL" : "BEAR"} v={report.regime_detail} />
            </Section>
          )}

          {report.readings && Object.keys(report.readings).length > 0 && (
            <Section title="Readings" accent="var(--green)">
              <Row k="RSI(14)" v={`${num(report.readings.rsi)}  (score ${num(report.readings.rsi_score)})`} />
              <Row k="MACD hist" v={`${num(report.readings.macd_histogram, 6)}  (score ${num(report.readings.macd_score)})`} />
              <Row k="4h uptrend" v={`${report.readings.htf_uptrend ? "yes" : "no"}  (score ${num(report.readings.ema_score)})`} />
              <Row k="Volume score" v={num(report.readings.volume_score)} />
              <Row k="Divergence score" v={num(report.readings.divergence_score)} />
              <Row k="ATR" v={`${num(report.readings.atr_pct, 2)}%`} />
              <Row k="Technical total" v={num(report.readings.technical_total)} />
            </Section>
          )}

          {report.whale && (
            <Section title="Whale spike" accent="var(--amber)">
              <Row k="Volume ratio" v={`${report.whale.volume_ratio.toFixed(1)}x`} />
              <Row k="Thrust" v={`${report.whale.thrust_pct >= 0 ? "+" : ""}${report.whale.thrust_pct.toFixed(1)}%`} />
              <Row k="As of" v={report.whale.as_of || "—"} />
            </Section>
          )}

          {[report.spot, report.whale_lane].filter(Boolean).map((lane) => (
            <LaneBlock key={lane!.lane} lane={lane!} />
          ))}
        </div>
      )}
    </main>
  );
}

function LaneBlock({ lane }: { lane: Lane }) {
  return (
    <div className="mb-2">
      <h3
        className="text-[10px] uppercase tracking-[0.2em] font-display mb-2 pb-1 border-b flex justify-between"
        style={{ color: lane.would_open ? "var(--green)" : "var(--red)", borderColor: "var(--border)" }}
      >
        <span>{lane.lane}</span>
        <span>{lane.would_open ? "would open" : `blocked · ${lane.blocked_by}`}</span>
      </h3>
      {!lane.would_open && (
        <p className="text-[12px] text-[var(--muted)] mb-2">{lane.reason}</p>
      )}
      <div className="flex flex-col gap-1">
        {lane.checks.map((c, i) => (
          <div key={i} className="flex justify-between text-[12px] gap-4">
            <span className="whitespace-nowrap" style={{ color: c.passed ? "var(--green)" : "var(--red)" }}>
              {c.passed ? "✓" : "✗"} <span className="text-[var(--muted)]">{c.name}</span>
            </span>
            <span className="tnum text-[var(--text)] text-right">{c.detail}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function Section({ title, accent, children }: { title: string; accent: string; children: React.ReactNode }) {
  return (
    <div>
      <h3
        className="text-[10px] uppercase tracking-[0.2em] font-display mb-2 pb-1 border-b"
        style={{ color: accent, borderColor: "var(--border)" }}
      >
        {title}
      </h3>
      <div className="flex flex-col gap-1.5">{children}</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between text-[12px] gap-4">
      <span className="text-[var(--muted)] whitespace-nowrap">{k}</span>
      <span className="tnum text-[var(--text)] text-right">{v}</span>
    </div>
  );
}
