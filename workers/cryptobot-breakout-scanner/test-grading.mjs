import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";

// node test-grading.mjs index.js [--baseline]. No credentials or network calls.
const [workerPath, mode] = process.argv.slice(2);
if (!workerPath || (mode && mode !== "--baseline")) {
  throw new Error("Usage: node test-grading.mjs <worker-bundle> [--baseline]");
}
const baseline = mode === "--baseline";
const source = await readFile(workerPath, "utf8");
const instrumented = source + "\nexport { scoreSetupQuality, runTick };\n";
const worker = await import("data:text/javascript;base64," + Buffer.from(instrumented).toString("base64"));

const threshold = {
  candleOpen: 0.0034453, candleHigh: 0.0034895, candleLow: 0.0034235, candleClose: 0.003466,
  pctAbove24hHigh: 0.67, turnoverEur: 17557, pre1hChangePct: 1.01, spreadPct: 0.35
};
const score = (changes) => worker.scoreSetupQuality({ ...threshold, ...changes });

test("T-EUR at 11:00 Vienna on Nov 25 is capped only by the new Grade A check", () => {
  const result = score({});
  assert.equal(result.grade, baseline ? "A" : "B");
  assert.equal(result.profile, baseline ? "DODO_PROFILE" : "");
  assert.deepEqual(result.failedChecks, baseline ? [] : ["positive_body_covers_upper_wick"]);
});
test("DODO-EUR at 09:30 Vienna on Nov 21 retains a clean close", () => {
  // Historical OHLC only. The archive did not record spread for this observation.
  const result = score({
    candleOpen: 0.032953, candleHigh: 0.034193, candleLow: 0.032582, candleClose: 0.033632,
    pctAbove24hHigh: 1.92, turnoverEur: 15754, pre1hChangePct: 4.78, spreadPct: null
  });
  assert.equal(result.grade, "A");
  assert.equal(result.profile, "DODO_PROFILE");
});
test("equal body and upper wick pass at different price scales", () => {
  for (const scale of [1e-5, 1, 1e5]) {
    const result = score({ candleOpen: 0.1 * scale, candleHigh: 0.3 * scale,
      candleLow: 0.01 * scale, candleClose: 0.2 * scale });
    assert.equal(result.grade, "A");
  }
});
test("one additional price tick of upper wick changes the result", () => {
  const result = score({ candleOpen: 0.1, candleHigh: 0.300001, candleLow: 0.01, candleClose: 0.2 });
  assert.equal(result.grade, baseline ? "A" : "B");
});
test("an unchanged close with no upper wick cannot receive grade A", () => {
  const result = score({ candleOpen: 0.003466, candleHigh: 0.003466 });
  assert.equal(result.grade, baseline ? "A" : "B");
  if (!baseline) assert.match(result.closeNote, /equal/i);
});
test("the severe red candle retains C without a redundant warning", () => {
  const result = score({ candleOpen: 0.00347 });
  assert.equal(result.grade, "C");
  assert.ok(!result.closeNote);
});
test("the existing wick limit still applies even when the body is large", () => {
  const result = score({ candleOpen: 0.11, candleHigh: 0.29, candleLow: 0.105, candleClose: 0.21 });
  assert.equal(result.grade, "B");
  assert.ok(!result.closeNote);
});
test("previous B or C stays unchanged even with the same weak candle body", () => {
  const borderline = score({ turnoverEur: 12000 });
  assert.equal(borderline.grade, "B");
  assert.ok(!borderline.closeNote);
  const severe = score({ spreadPct: 1.1 });
  assert.equal(severe.grade, "C");
  assert.ok(!severe.closeNote);
});

test("a real scan tick stays confirmed and propagates the grade to storage and both alert formats", async () => {
  const quarter = 15 * 60 * 1000;
  const close = Date.parse("2026-11-25T10:00:00Z");
  const signal = close - quarter;
  // Synthetic context fixes the prior high and activity. Only the tested signal bar is historical.
  const prior = Array.from({ length: 97 }, (_, i) =>
    [signal - (97 - i) * quarter, 0.00344, 0.0034429, 0.00342, 0.00344, 142500]);
  const rows = [...prior, [signal, threshold.candleOpen, threshold.candleHigh,
    threshold.candleLow, threshold.candleClose, 5065556.61739924]];
  let staged, telegram, webhook;
  const cycle = { closeTimeMs: close, markets: ["T-EUR"], pending: ["T-EUR"], checked: [],
    completed: false, alertsCount: 0, missingCount: 0 };
  const storage = {
    getScanCycle: () => cycle, saveScanCycle() {}, getMeta: () => null, setMeta() {},
    listActiveSetupsDue: () => [], claimPendingFollowupEvents: () => [], getPriorBreakout: () => null,
    savePriorBreakout() {}, markInitialAlert: () => true,
    stageSetup(setup) { staged = setup; return true; }
  };
  const result = await worker.runTick({
    nowFn: () => close + 60000,
    env: { COOLDOWN_MINUTES: "0", TELEGRAM_BOT_TOKEN: "fixture", TELEGRAM_CHAT_ID: "fixture",
      ALERT_WEBHOOK_URL: "https://fixture.invalid/alerts" },
    storage,
    fetchFn: async (url, request) => {
      const { pathname, hostname } = new URL(url);
      if (pathname.endsWith("/candles")) return Response.json(rows);
      if (pathname.endsWith("/book")) return Response.json({ bids: [["0.00346","1"]], asks: [["0.00347","1"]] });
      if (hostname === "api.telegram.org") telegram = JSON.parse(request.body).text;
      else if (hostname === "fixture.invalid") webhook = JSON.parse(request.body);
      else throw new Error("Unexpected request " + pathname);
      return new Response();
    }
  });
  assert.deepEqual(result.errors, []);
  assert.equal(result.alertsTriggered, 1);
  assert.equal(staged.setupGrade, baseline ? "A" : "B");
  assert.equal(staged.setupProfile, baseline ? "DODO_PROFILE" : "");
  assert.equal(webhook.event_type, "confirmed_breakout");
  assert.equal(webhook.grade, staged.setupGrade);
  assert.match(telegram, new RegExp("Grade " + staged.setupGrade));
  assert.match(telegram, /Moderate confidence/);
  assert.equal(telegram.includes("Upper wick exceeded the candle body"), !baseline);
  assert.equal(webhook.detail.includes("Upper wick exceeded the candle body"), !baseline);
});
