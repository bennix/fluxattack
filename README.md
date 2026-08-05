# FLUX Attack Evaluation Artifacts

This repository contains the **attack evaluation code** and **experimental data** accompanying our study of the FLUX topology CAPTCHA (server-held answer, global coherent warping, behavioral risk scoring, and invisible SHA-256 proof-of-work).

## Purpose

These artifacts support **reproducibility** of the reported adversarial evaluation:

- offline content / boundary matching attack sweeps (v5 deformation strength)
- live API defensive audit against a local FLUX v5 Flask deployment
- browser instrumentation probes (Playwright)

> **Ethics / scope.** The materials are for evaluating the authors' own CAPTCHA design under authorized local testing. Do not use them to attack third-party production systems.

## Repository layout

```text
attack/          Offline attack scripts and unit/integration tests
audit/           live defensive audit harnesses (API + browser)
data/            raw JSON results used in the paper / audit reports
docs/            short data dictionary and reproduction notes
```

## Key files

| Path | Description |
|------|-------------|
| `attack/attack_v5.js` | v5 content-matching / deformation-sweep attacker |
| `attack/test_flask_v5.py` | Flask v5 security regression tests |
| `attack/browser_flask_v5.js` | browser exposure / rendering sanity probe |
| `audit/live_audit_5056.py` | live API audit: recon, PoW solve, visual+PoW campaign |
| `audit/browser_probe_5056.js` | Playwright probe for secrets exposure and UI attack button |
| `data/results_v5.json` | offline GW sweep success rates (300 trials / setting) |
| `data/live_audit_results_round2.json` | live retest summary (PoW difficulty = 16) |
| `data/browser_probe_results_rerun.json` | live browser retest summary |

## Main quantitative findings (live retest)

- Visual matching + full PoW end-to-end pass rate: **0/20 (0%)**
- PoW difficulty: **16 bits × 9 tasks**, mean solve time ≈ **0.49 s**
- Cross-session, forged client score, missing PoW, and post-failure replay: **all blocked**
- In-page boundary-matching demo attack: redirected to `/failed`

## How to reproduce (live audit)

1. Start the local FLUX v5 Flask service (default audit target: `http://127.0.0.1:5056/`).
2. Run:

```bash
python3 audit/live_audit_5056.py
```

3. Optional browser probe (requires Playwright + Chromium):

```bash
node audit/browser_probe_5056.js
```

## Citation

If you use these artifacts, please cite the associated article and this repository URL:

`https://github.com/bennix/fluxattack`

## License

Research artifacts released for academic reproducibility. See the article for licensing terms if separately specified.
