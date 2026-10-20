"""US retail forex rules.

Each rule cites the section a compliance officer should read to confirm a finding.
These rules apply to the DEALER (FDM/RFED) handling customer accounts.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from lynxguard.core import Citation, Finding, ParameterSet, Rule, Severity

from .models import (
    AdjustmentReason,
    ConfirmationCheckRequest,
    DepositCheckRequest,
    FundingMethod,
    OpenLot,
    PriceAdjustmentCheckRequest,
    Side,
    TradeCheckRequest,
)

GUIDE_URL = "https://www.nfa.futures.org/members/member-resources/files/forex-regulatory-guide.html"
FR12_URL = "https://www.nfa.futures.org/rulebooksql/rules.aspx?Section=7&RuleID=SECTION+12"
R243_URL = "https://www.nfa.futures.org/rulebooksql/rules.aspx?RuleID=RULE+2-43&Section=4"
CFR5_URL = "https://www.law.cornell.edu/cfr/text/17/part-5"

C_FR12 = Citation(source="NFA Financial Requirements", section="Section 12(a)", url=FR12_URL)
C_CFR59 = Citation(source="CFTC Regulation", section="17 CFR 5.9", url=CFR5_URL)
C_243B = Citation(source="NFA Compliance Rule", section="2-43(b)", url=R243_URL)
C_243A = Citation(source="NFA Compliance Rule", section="2-43(a)", url=R243_URL)
C_236K = Citation(source="NFA Compliance Rule", section="2-36(k)", url=GUIDE_URL)
C_CFR513 = Citation(source="CFTC Regulation", section="17 CFR 5.13", url=CFR5_URL)
C_I1433 = Citation(source="NFA Notice to Members", section="I-14-33 (credit card funding)", url=GUIDE_URL)


def _f(rule_id, sev, msg, cites, version=None, **evidence) -> Finding:
    return Finding(
        rule_id=rule_id,
        severity=sev.name,
        message=msg,
        citations=list(cites),
        parameter_version=version,
        evidence={k: str(v) if isinstance(v, (Decimal, datetime, date)) else v for k, v in evidence.items()},
    )


# --------------------------------------------------------------------------- #
# FX-SD-001  Security deposit (leverage)
# --------------------------------------------------------------------------- #

def deposit_pct(pair: str, p: dict) -> Decimal:
    """Required deposit percent for a pair. The highest applicable rate wins."""
    currencies = [c.strip().upper() for c in pair.replace("-", "/").split("/")]
    if len(currencies) != 2 or not all(len(c) == 3 for c in currencies):
        raise ValueError(f"pair must look like 'EUR/USD', got {pair!r}")
    majors = set(p["major_currencies"])
    neutral = set(p.get("neutral_currencies", []))
    overrides = {k: Decimal(str(v)) for k, v in (p.get("temporary_overrides") or {}).items()}
    rates = []
    for c in currencies:
        if c in neutral:
            continue
        base = Decimal(p["major_currency_pct"]) if c in majors else Decimal(p["other_pct"])
        rates.append(max(base, overrides.get(c, base)))
    # A pair of two neutral currencies cannot occur for a US dealer, but be safe.
    return max(rates) if rates else Decimal(p["other_pct"])


def _eval_security_deposit(req: TradeCheckRequest, params: ParameterSet) -> list[Finding]:
    p = params.resolve("security_deposit", req.trade.executed_at.date())
    rid, ver = "FX-SD-001", p["version"]
    positions: list[tuple[str, str, Decimal]] = [
        (lot.lot_id, lot.pair, lot.notional_usd) for lot in req.account.open_lots
    ]
    if req.trade.position_effect == "open":
        positions.append((req.trade.trade_id, req.trade.pair, req.trade.notional_usd))
    else:
        # A close reduces exposure: consume the named lots in order, scaling a
        # partially closed lot's notional to what stays open.
        lots = {lot.lot_id: lot for lot in req.account.open_lots}
        left = req.trade.quantity
        remaining_notional: dict[str, Decimal] = {}
        for lid in req.trade.closes_lot_ids:
            lot = lots[lid]
            used = min(left, lot.quantity)
            left -= used
            remaining_notional[lid] = lot.notional_usd * (lot.quantity - used) / lot.quantity
        positions = [
            (pid, pair, remaining_notional.get(pid, notional))
            for pid, pair, notional in positions
            if remaining_notional.get(pid, notional) > 0
        ]

    lines = []
    required = Decimal(0)
    for pid, pair, notional in positions:
        pct = deposit_pct(pair, p)
        need = notional * pct / Decimal(100)
        required += need
        lines.append({"position": pid, "pair": pair, "notional_usd": str(notional),
                      "pct": str(pct), "required_usd": str(need)})

    held = req.account.security_deposit_usd
    if held < required:
        shortfall = required - held
        return [_f(
            rid, Severity.VIOLATION,
            f"Security deposit ${held} is below the required ${required.quantize(Decimal('0.01'))} "
            f"(short ${shortfall.quantize(Decimal('0.01'))}). The dealer must collect more or liquidate.",
            (C_FR12, C_CFR59), ver, held_usd=held, required_usd=required, positions=lines,
        )]
    return []


SECURITY_DEPOSIT = Rule(
    rule_id="FX-SD-001",
    title="Minimum security deposit on retail forex positions",
    citations=(C_FR12, C_CFR59),
    event_type="trade",
    evaluate=_eval_security_deposit,
    parameter_group="security_deposit",
)


# --------------------------------------------------------------------------- #
# FX-FIFO-001  No offsetting positions; close first-in, first-out
# --------------------------------------------------------------------------- #

def _eval_fifo(req: TradeCheckRequest, params: ParameterSet) -> list[Finding]:
    rid = "FX-FIFO-001"
    t = req.trade
    same_pair = [lot for lot in req.account.open_lots if lot.pair == t.pair]
    opposite = sorted(
        (lot for lot in same_pair if lot.side == t.side.opposite),
        key=lambda lot: (lot.opened_at, lot.lot_id),
    )

    if t.position_effect == "open":
        if opposite:
            return [_f(
                rid, Severity.VIOLATION,
                f"Opening a {t.side.value} {t.pair} position while {len(opposite)} opposite "
                f"{t.side.opposite.value} lot(s) are open would carry offsetting positions. "
                "The new order must offset the oldest open lot instead.",
                (C_243B,), opposite_lot_ids=[lot.lot_id for lot in opposite],
            )]
        return []

    # Closing trade.
    if not opposite:
        return [_f(rid, Severity.REVIEW,
                   f"Close order on {t.pair} but no opposite open lots were supplied.",
                   (C_243B,))]
    if not t.closes_lot_ids:
        return [_f(rid, Severity.REVIEW,
                   "Lot allocation for this close was not supplied, so FIFO cannot be verified.",
                   (C_243B,))]

    if t.customer_directed_offset:
        # Allowed: offset a same-size lot, but it must be the OLDEST lot of that size.
        same_size = [lot for lot in opposite if lot.quantity == t.quantity]
        if not same_size:
            return [_f(rid, Severity.VIOLATION,
                       f"Customer-directed offset of {t.quantity} but no open lot has that size.",
                       (C_243B,))]
        expected = same_size[0].lot_id
        if t.closes_lot_ids != [expected]:
            return [_f(rid, Severity.VIOLATION,
                       f"Customer-directed offset must close the oldest lot of size {t.quantity} "
                       f"({expected}), not {t.closes_lot_ids}.",
                       (C_243B,), expected_lot_id=expected, actual=t.closes_lot_ids)]
        return []

    # Strict FIFO: consume oldest lots first until the close quantity is used up.
    expected: list[str] = []
    remaining = t.quantity
    for lot in opposite:
        if remaining <= 0:
            break
        expected.append(lot.lot_id)
        remaining -= lot.quantity
    if t.closes_lot_ids != expected:
        return [_f(rid, Severity.VIOLATION,
                   f"Close does not follow FIFO. Expected lots {expected}, got {t.closes_lot_ids}.",
                   (C_243B,), expected_lot_ids=expected, actual=t.closes_lot_ids)]
    if remaining < 0:
        pass  # final lot partially closed - allowed
    elif remaining > 0:
        return [_f(rid, Severity.REVIEW,
                   f"Close quantity exceeds open opposite lots by {remaining}; the excess would "
                   "open a new position. Confirm how the platform books it.",
                   (C_243B,), excess=remaining)]
    return []


FIFO = Rule(
    rule_id="FX-FIFO-001",
    title="No offsetting positions; offset first-in, first-out",
    citations=(C_243B,),
    event_type="trade",
    evaluate=_eval_fifo,
)


# --------------------------------------------------------------------------- #
# FX-ADJ-001  Price adjustments and cancellations
# --------------------------------------------------------------------------- #

def _eval_price_adjustment(req: PriceAdjustmentCheckRequest, params: ParameterSet) -> list[Finding]:
    p = params.resolve("price_adjustment", req.adjusted_at.date())
    rid, ver = "FX-ADJ-001", p["version"]
    out: list[Finding] = []

    if req.reason is AdjustmentReason.OTHER:
        out.append(_f(rid, Severity.VIOLATION,
                      "Executed orders may only be cancelled or repriced for the reasons the rule "
                      "permits. 'Other' is not one of them.", (C_243A,), ver))

    if req.reason in (AdjustmentReason.CUSTOMER_COMPLAINT, AdjustmentReason.PLATFORM_ERROR):
        if not req.favorable_to_customer:
            out.append(_f(rid, Severity.VIOLATION,
                          f"An adjustment for '{req.reason.value}' is only permitted when it favors "
                          "the customer.", (C_243A,), ver))

    if req.reason is AdjustmentReason.COUNTERPARTY_STP:
        if not (req.dealer_uses_stp_exclusively and req.counterparty_unaffiliated):
            out.append(_f(rid, Severity.VIOLATION,
                          "Counterparty-driven adjustments are only allowed when the dealer uses "
                          "straight-through processing exclusively with an unaffiliated counterparty.",
                          (C_243A,), ver))
        limit = timedelta(minutes=int(p["counterparty_notice_minutes"]))
        if req.customer_notified_at is None:
            out.append(_f(rid, Severity.VIOLATION,
                          "No written customer notice recorded for a counterparty-driven adjustment.",
                          (C_243A,), ver))
        elif req.customer_notified_at - req.executed_at > limit:
            late = req.customer_notified_at - req.executed_at
            out.append(_f(rid, Severity.VIOLATION,
                          f"Customer notified {late} after execution; the limit is {limit}.",
                          (C_243A,), ver, executed_at=req.executed_at,
                          notified_at=req.customer_notified_at))
        if not req.counterparty_documentation_provided:
            out.append(_f(rid, Severity.VIOLATION,
                          "The customer notice must include the counterparty's documentation.",
                          (C_243A,), ver))
        if not req.window_trades:
            out.append(_f(rid, Severity.REVIEW,
                          "No list of other trades in the same time period and pair was supplied, so "
                          "uniform treatment (buys and sells alike) cannot be verified.",
                          (C_243A,), ver))
        else:
            adjusted = set(req.adjusted_trade_ids) | {req.trade_id}
            skipped = [w for w in req.window_trades if w.trade_id not in adjusted]
            if skipped:
                out.append(_f(rid, Severity.REVIEW,
                              f"{len(skipped)} trade(s) in the same period and pair were not adjusted. "
                              "This is only allowed if the dealer chose to honor profitable trades for ALL "
                              "similarly situated customers. Confirm before proceeding.",
                              (C_243A,), ver,
                              skipped=[{"trade_id": w.trade_id, "side": w.side.value} for w in skipped]))

    if not req.principal_approval_documented:
        out.append(_f(rid, Severity.VIOLATION,
                      "A listed principal who is also an NFA Associate must approve the adjustment in "
                      "writing, with supporting documents.", (C_243A,), ver))
    return out


PRICE_ADJUSTMENT = Rule(
    rule_id="FX-ADJ-001",
    title="Cancellations and price adjustments of executed customer orders",
    citations=(C_243A,),
    event_type="price_adjustment",
    evaluate=_eval_price_adjustment,
    parameter_group="price_adjustment",
)


# --------------------------------------------------------------------------- #
# FX-CONF-001  Written confirmation by the next business day
# --------------------------------------------------------------------------- #

def confirmation_deadline(executed_at: datetime, p: dict) -> datetime:
    tz = ZoneInfo(p["business_day_timezone"])
    holidays = {date.fromisoformat(str(h)) for h in p.get("holidays", [])}
    d = executed_at.astimezone(tz).date() + timedelta(days=1)
    while d.weekday() >= 5 or d in holidays:
        d += timedelta(days=1)
    return datetime.combine(d, time(23, 59, 59), tzinfo=tz)


def _eval_confirmation(req: ConfirmationCheckRequest, params: ParameterSet) -> list[Finding]:
    p = params.resolve("confirmation", req.executed_at.date())
    rid, ver = "FX-CONF-001", p["version"]
    deadline = confirmation_deadline(req.executed_at, p)
    cites = (C_236K, C_CFR513)
    if req.confirmation_sent_at is not None:
        if req.confirmation_sent_at > deadline:
            return [_f(rid, Severity.VIOLATION,
                       f"Confirmation sent after the next-business-day deadline ({deadline.isoformat()}).",
                       cites, ver, deadline=deadline, sent_at=req.confirmation_sent_at)]
        return []
    if req.as_of > deadline:
        return [_f(rid, Severity.VIOLATION,
                   f"No confirmation sent and the deadline ({deadline.isoformat()}) has passed.",
                   cites, ver, deadline=deadline)]
    return [_f(rid, Severity.INFO,
               f"Confirmation not yet sent; due by {deadline.isoformat()}.", cites, ver, deadline=deadline)]


CONFIRMATION = Rule(
    rule_id="FX-CONF-001",
    title="Written trade confirmation by the next business day",
    citations=(C_236K, C_CFR513),
    event_type="confirmation",
    evaluate=_eval_confirmation,
    parameter_group="confirmation",
)


# --------------------------------------------------------------------------- #
# FX-FUND-001  No credit card funding
# --------------------------------------------------------------------------- #

def _eval_funding(req: DepositCheckRequest, params: ParameterSet) -> list[Finding]:
    rid = "FX-FUND-001"
    if req.funding_method in (FundingMethod.CREDIT_CARD, FundingMethod.CREDIT_CARD_LINKED):
        return [_f(rid, Severity.VIOLATION,
                   "Retail forex accounts may not be funded by credit card or by any method that "
                   "draws on a credit card.", (C_I1433,), method=req.funding_method.value)]
    if req.funding_method is FundingMethod.ACH and req.dealer_can_distinguish_credit_cards is not True:
        return [_f(rid, Severity.REVIEW,
                   "Electronic debits are only acceptable if the dealer can tell them apart from "
                   "credit card funding and reject cards before accepting funds. Not confirmed.",
                   (C_I1433,))]
    if req.funding_method is FundingMethod.OTHER:
        return [_f(rid, Severity.REVIEW,
                   "Funding method is 'other'. Confirm it does not draw on a credit card.", (C_I1433,))]
    return []


NO_CREDIT_CARD_FUNDING = Rule(
    rule_id="FX-FUND-001",
    title="No credit card funding of retail forex accounts",
    citations=(C_I1433,),
    event_type="deposit",
    evaluate=_eval_funding,
)


RULES: list[Rule] = [SECURITY_DEPOSIT, FIFO, PRICE_ADJUSTMENT, CONFIRMATION, NO_CREDIT_CARD_FUNDING]
