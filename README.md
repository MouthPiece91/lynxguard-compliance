# LynxGuard Compliance

Rule-based compliance checks for regulated trading. A dealer's system submits a
trade or account event; LynxGuard runs deterministic rules and returns every
finding with **the exact regulation section to read**. A human reviewer then
records PROCEED or CANCEL. Every check and decision goes into a tamper-evident
audit trail an examiner can verify.

First regime: **US retail forex** (CFTC Part 5 / NFA rules for forex dealer
members). Next: **bank risk-limit governance** for small and mid-size banks.

## Principles

1. **Rules decide, not the AI.** Each rule is deterministic code with tests and a
   citation. AI may later help draft rules from regulation text and explain
   findings, but a person signs off on every rule.
2. **Numbers are versioned data.** Thresholds live in `parameters.yaml` with a
   source link and verification date. Changing a value means adding a version,
   so any past check can be replayed under the rule in force at the time.
3. **Never guess.** If the data needed is missing, or no verified parameters
   cover the date, the result is REVIEW, not PASS.
4. **The engine reports; people decide.** It never executes or blocks trades.
5. **Org isolation and audit from day one.** Every API key belongs to one
   organization; each org has its own hash-chained, append-only audit log.

## Outcomes

| Outcome | Meaning |
|---|---|
| PASS | No findings, or informational only |
| REVIEW | A person must look — the data given cannot settle it |
| VIOLATION | A rule is breached; the finding cites the section |

## Quick start

```bash
pip install -e '.[dev]'
pytest -q

# See it work: six sample trades, findings, a decision and the audit check
python scripts/demo.py

# Create an API key for an organization
python -m lynxguard.auth new --org demo --name "demo desk"
# Save the printed JSON entry to keys.json, then:
LYNXGUARD_KEYS_FILE=keys.json uvicorn lynxguard.api:build_app --factory --reload
# Interactive docs: http://localhost:8000/docs
```

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/rules` | Rule catalog with citations |
| POST | `/v1/forex-us/checks/trade` | Security deposit + FIFO/offsetting |
| POST | `/v1/forex-us/checks/price-adjustment` | Cancellations and repricing |
| POST | `/v1/forex-us/checks/confirmation` | Next-business-day confirmation |
| POST | `/v1/forex-us/checks/deposit` | Funding method |
| GET | `/v1/checks/{id}` | A check plus its decisions |
| POST | `/v1/checks/{id}/decision` | Record PROCEED / CANCEL with reviewer and reason |
| GET | `/v1/audit`, `/v1/audit/verify` | Audit trail and chain verification |

All `/v1` calls except `/v1/rules` need an `X-API-Key` header.

## Layout

```
src/lynxguard/
  core.py                 rule framework, findings, parameter versioning
  audit.py                append-only hash-chained audit log (SQLite for v1)
  auth.py                 per-organization API keys (hashes only)
  api.py                  FastAPI app
  regimes/forex_us/
    models.py             input events
    rules.py              rules with citations
    parameters.yaml       versioned thresholds with sources
docs/rule-catalog.md      what each rule checks, sources, open assumptions
```

## Roadmap

- [x] Forex v1: security deposit, FIFO/offsetting, price adjustments, confirmations, funding
- [ ] Forex: slippage symmetry and customer-priority surveillance (aggregate data)
- [ ] Forex phase 2: account opening, disclosures, KYC
- [ ] Postgres storage, reviewer UI
- [ ] Bank risk-limit governance regime (small/mid-size banks)

Not legal advice. Every rule must be reviewed by compliance counsel before production use.
