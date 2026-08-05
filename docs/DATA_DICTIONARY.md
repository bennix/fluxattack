# Data dictionary

## `data/results_v5.json`

Offline deformation-strength sweep for FLUX v5.

| Field | Meaning |
|-------|---------|
| `trials` | Number of trials per GW setting (300) |
| `sweep[].gw` | Global warp amplitude |
| `sweep[].oracle_static` | Success rate when attacker knows the glyph |
| `sweep[].bf_static` | Brute-force (13-glyph) static matching success rate |
| `sweep[].bf_robust` | Robust (low-pass) matching success rate |

## `data/live_audit_results_round2.json`

Live retest against `http://127.0.0.1:5056/` on 2026-08-04.

| Field | Meaning |
|-------|---------|
| `recon.pow_difficulty` | Invisible PoW difficulty bits (16 in retest) |
| `pow_benchmark.elapsed_mean_s` | Mean wall-clock time to solve all tile PoW tasks |
| `visual_attack_campaign.pass_rate` | End-to-end pass rate of visual matcher + PoW |
| `negative.*` | Negative-control cases (must fail) |
| `summary.broken` | Whether the live campaign is considered broken (>=25% pass) |

## `data/browser_probe_results_rerun.json`

Playwright retest:

| Field | Meaning |
|-------|---------|
| `summary.secrets_not_exposed` | Answer-related globals are undefined |
| `summary.inpage_attack_landed_failed` | Built-in attack button led to `/failed` |
| `summary.untrusted_click_no_navigation` | Synthetic `click()` did not submit |
