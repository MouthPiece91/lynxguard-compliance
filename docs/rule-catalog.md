# Rule catalog: US retail forex (`forex_us`)

These rules apply to **forex dealer members (FDMs/RFEDs)** handling retail customer
accounts. Every number lives in `src/lynxguard/regimes/forex_us/parameters.yaml`
with its source and verification date.

**Verified against official sources on 2026-10-19.** This is not legal advice;
compliance counsel must review each rule before production use.

| Rule ID | What it checks | Citation | Status |
|---|---|---|---|
| FX-SD-001 | Security deposit ≥ 2% of notional for pairs in GBP, CHF, CAD, JPY, EUR, AUD, NZD, SEK, NOK, DKK; 5% otherwise; highest rate wins for mixed pairs; temporary increases supported | NFA Financial Requirements §12(a); 17 CFR 5.9 | Built |
| FX-FIFO-001 | No offsetting positions; offsets first-in, first-out; customer-directed same-size offset must hit the oldest lot of that size | NFA Compliance Rule 2-43(b) | Built |
| FX-ADJ-001 | Cancellations/price adjustments only for permitted reasons; 15-minute written notice with counterparty documents; uniform buy/sell treatment; written principal approval | NFA Compliance Rule 2-43(a) | Built |
| FX-CONF-001 | Written confirmation no later than the next business day | NFA Compliance Rule 2-36(k); 17 CFR 5.13 | Built |
| FX-FUND-001 | No credit card funding, or funding that draws on a credit card | NFA Notice I-14-33 | Built |
| FX-SLIP-001 | Slippage applied the same way whether the market moves for or against the customer; fills outside displayed price range | NFA 2-36(e) Interpretive Notice | Planned (needs aggregate fill data) |
| FX-PRIO-001 | Executable customer orders filled before dealer/related proprietary orders | 17 CFR 5.18(a)(2) | Planned (needs dealer order flow) |
| FX-KYC-* | Risk disclosure and signed acknowledgment, customer information, annual verification | 17 CFR 5.5(b); NFA 2-36, 2-9 | Phase 2 |

## Assumptions to confirm with counsel

1. **USD is neutral for §12.** Section 12 lists ten currencies and does not list USD.
   NFA's Forex Regulatory Guide gives EUR/USD as 2% and USD/MXN as 5%, so the
   non-USD currency decides the rate.
2. **Mixed pairs take the higher rate.** Stated in the Regulatory Guide; §12 itself is silent.
3. **Business day for confirmations** is measured in `America/New_York` and the
   holiday list is empty until counsel confirms which calendar applies.
4. **Earliest verified date.** The current §12 percentages are only asserted from
   2026-10-19 (the verification date). Checks dated earlier return REVIEW instead
   of guessing which percentages applied then.

## Sources

- NFA Forex Transactions: Regulatory Guide (Sept 2022 revisions) —
  https://www.nfa.futures.org/members/member-resources/files/forex-regulatory-guide.html
- NFA Financial Requirements Section 12 —
  https://www.nfa.futures.org/rulebooksql/rules.aspx?Section=7&RuleID=SECTION+12
- NFA Compliance Rule 2-43 —
  https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=RULE+2-43&Section=4
- 17 CFR Part 5 — https://www.law.cornell.edu/cfr/text/17/part-5
