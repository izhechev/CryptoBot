# Confirmed breakout grading

This directory holds the change relative to the production Telegram Worker bundle from Cloudflare version `21feb941-dd5a-4977-aa01-d3dcdb24c916`. The original TypeScript project is maintained separately. When rebuilding that project, carry the same grade cap into `scoreSetupQuality` in `src/breakout.ts` and forward the cap reason to the alert formatters. Run the regression suite against the rebuilt bundle before publishing it.

| Bundle | SHA-256 |
|---|---|
| Original | `9c34f824442918c8cf3ef9287bc70ef5c66a835550a33c96b334f0846ce9a681` |
| With grading change | `267f7d2cafe0d0690b5a3d477fa1cbf8b12509fe3f4c460aa78f8314620148c9` |

To reproduce the release, put a copy of the original `index.js` alongside the patch and test in a working directory, then run these commands there with Node 22 or newer.

```sh
node test-grading.mjs index.js --baseline
patch -p1 < worker-grade.patch
node test-grading.mjs index.js
```

The tests exercise the grade, stored setup, Telegram notification and webhook using local fixtures. The [rules and cohort definition](../../docs/v1/experiments/2026-11-24-bitvavo-paper-log-archive/Rules.md#grade-a-on-a-confirmed-breakout) describe how to evaluate the change on subsequent alerts.
