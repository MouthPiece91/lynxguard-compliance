from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from lynxguard.core import ParameterSet, UnverifiedParameters, run_rules
from lynxguard.regimes.forex_us import RULES, load_parameters
from lynxguard.regimes.forex_us.models import (
    ConfirmationCheckRequest,
    DepositCheckRequest,
    PriceAdjustmentCheckRequest,
    TradeCheckRequest,
)
from lynxguard.regimes.forex_us.rules import confirmation_deadline, deposit_pct

UTC = timezone.utc
T0 = datetime(2026, 10, 20, 14, 0, tzinfo=UTC)  # Tuesday
P = load_parameters()


def lot(lot_id, side, qty, minutes_ago, pair="EUR/USD", notional=None):
    return {
        "lot_id": lot_id, "pair": pair, "side": side, "quantity": qty, "price": "1.08",
        "opened_at": (T0 - timedelta(minutes=minutes_ago)).isoformat(),
        "notional_usd": notional if notional is not None else qty,
    }


def trade_req(trade, lots=(), deposit="100000"):
    base = {"trade_id": "T1", "pair": "EUR/USD", "price": "1.08", "executed_at": T0.isoformat()}
    base.update(trade)
    base.setdefault("notional_usd", base["quantity"])
    return TradeCheckRequest.model_validate(
        {"account": {"account_id": "A1", "security_deposit_usd": deposit, "open_lots": list(lots)},
         "trade": base}
    )


def check(event_type, req):
    return run_rules(event_type, req, RULES, P)


def ids(result, rule_id):
    return [f for f in result.findings if f.rule_id == rule_id]


# ---------------- security deposit ---------------- #

@pytest.mark.parametrize("pair,pct", [
    ("EUR/USD", "2"), ("USD/JPY", "2"), ("GBP/CHF", "2"), ("CAD/JPY", "2"),
    ("USD/MXN", "5"), ("BRL/MXN", "5"), ("EUR/TRY", "5"),
])
def test_deposit_pct_matches_nfa_examples(pair, pct):
    p = P.resolve("security_deposit", date(2026, 10, 20))
    assert deposit_pct(pair, p) == Decimal(pct)


def test_temporary_override_raises_rate():
    p = dict(P.resolve("security_deposit", date(2026, 10, 20)), temporary_overrides={"CHF": "5"})
    assert deposit_pct("EUR/CHF", p) == Decimal("5")


def test_deposit_sufficient_passes():
    # 100,000 notional EUR/USD needs 2,000.
    r = check("trade", trade_req({"side": "BUY", "quantity": "100000", "position_effect": "open"},
                                 deposit="2000"))
    assert not ids(r, "FX-SD-001")


def test_deposit_shortfall_is_violation_with_citation():
    r = check("trade", trade_req({"side": "BUY", "quantity": "100000", "position_effect": "open",
                                  "pair": "USD/MXN"}, deposit="2000"))
    (f,) = ids(r, "FX-SD-001")
    assert f.severity == "VIOLATION"
    assert f.evidence["required_usd"] == "5000"
    assert any(c.section == "Section 12(a)" for c in f.citations)
    assert r.outcome == "VIOLATION"


def test_partial_close_keeps_remaining_exposure():
    lots = [lot("L1", "BUY", "100000", 30)]
    # Close half; 50,000 remains open, needs 1,000. Deposit 999 must fail.
    r = check("trade", trade_req({"side": "SELL", "quantity": "50000", "position_effect": "close",
                                  "closes_lot_ids": ["L1"]}, lots, deposit="999"))
    assert ids(r, "FX-SD-001")


def test_checks_before_verified_date_do_not_guess():
    with pytest.raises(UnverifiedParameters):
        P.resolve("security_deposit", date(2020, 1, 1))
    old = trade_req({"side": "BUY", "quantity": "1000", "position_effect": "open",
                     "executed_at": "2020-01-02T10:00:00+00:00"})
    r = check("trade", old)
    (f,) = ids(r, "FX-SD-001")
    assert f.severity == "REVIEW" and "could not run" in f.message


# ---------------- FIFO / offsetting ---------------- #

def test_opening_hedge_is_violation():
    lots = [lot("L1", "BUY", "10000", 30)]
    r = check("trade", trade_req({"side": "SELL", "quantity": "10000", "position_effect": "open"}, lots))
    (f,) = ids(r, "FX-FIFO-001")
    assert f.severity == "VIOLATION" and f.citations[0].section == "2-43(b)"


def test_adding_same_side_is_fine():
    lots = [lot("L1", "BUY", "10000", 30)]
    r = check("trade", trade_req({"side": "BUY", "quantity": "10000", "position_effect": "open"}, lots))
    assert not ids(r, "FX-FIFO-001")


def test_fifo_close_oldest_first_passes():
    lots = [lot("NEW", "BUY", "10000", 5), lot("OLD", "BUY", "10000", 60)]
    r = check("trade", trade_req({"side": "SELL", "quantity": "15000", "position_effect": "close",
                                  "closes_lot_ids": ["OLD", "NEW"]}, lots))
    assert not ids(r, "FX-FIFO-001")


def test_closing_newest_first_is_violation():
    lots = [lot("NEW", "BUY", "10000", 5), lot("OLD", "BUY", "10000", 60)]
    r = check("trade", trade_req({"side": "SELL", "quantity": "10000", "position_effect": "close",
                                  "closes_lot_ids": ["NEW"]}, lots))
    (f,) = ids(r, "FX-FIFO-001")
    assert f.severity == "VIOLATION" and f.evidence["expected_lot_ids"] == ["OLD"]


def test_customer_directed_same_size_offset_must_be_oldest_of_that_size():
    lots = [lot("A", "BUY", "20000", 90), lot("B", "BUY", "10000", 60), lot("C", "BUY", "10000", 10)]
    ok = check("trade", trade_req({"side": "SELL", "quantity": "10000", "position_effect": "close",
                                   "closes_lot_ids": ["B"], "customer_directed_offset": True}, lots))
    assert not ids(ok, "FX-FIFO-001")
    bad = check("trade", trade_req({"side": "SELL", "quantity": "10000", "position_effect": "close",
                                    "closes_lot_ids": ["C"], "customer_directed_offset": True}, lots))
    assert ids(bad, "FX-FIFO-001")[0].severity == "VIOLATION"


def test_close_without_allocation_needs_review():
    lots = [lot("L1", "BUY", "10000", 30)]
    r = check("trade", trade_req({"side": "SELL", "quantity": "10000", "position_effect": "close"}, lots))
    assert ids(r, "FX-FIFO-001")[0].severity == "REVIEW"


def test_unknown_lot_id_rejected_at_input():
    with pytest.raises(ValueError):
        trade_req({"side": "SELL", "quantity": "1", "position_effect": "close", "closes_lot_ids": ["X"]})


# ---------------- price adjustments ---------------- #

def adj(**kw):
    base = {
        "adjustment_id": "ADJ1", "trade_id": "T1", "pair": "EUR/USD",
        "executed_at": T0.isoformat(), "adjusted_at": (T0 + timedelta(minutes=5)).isoformat(),
        "reason": "counterparty_stp", "favorable_to_customer": False,
        "customer_notified_at": (T0 + timedelta(minutes=10)).isoformat(),
        "counterparty_documentation_provided": True, "dealer_uses_stp_exclusively": True,
        "counterparty_unaffiliated": True, "principal_approval_documented": True,
        "window_trades": [{"trade_id": "T1", "side": "BUY"}, {"trade_id": "T2", "side": "SELL"}],
        "adjusted_trade_ids": ["T1", "T2"],
    }
    base.update(kw)
    return PriceAdjustmentCheckRequest.model_validate(base)


def test_compliant_counterparty_adjustment_passes():
    assert check("price_adjustment", adj()).outcome == "PASS"


def test_late_notice_is_violation():
    r = check("price_adjustment", adj(customer_notified_at=(T0 + timedelta(minutes=16)).isoformat()))
    assert any("limit is 0:15:00" in f.message for f in r.findings)
    assert r.outcome == "VIOLATION"


def test_one_sided_adjustment_flags_skipped_trades():
    r = check("price_adjustment", adj(adjusted_trade_ids=["T1"]))
    (f,) = r.findings
    assert f.severity == "REVIEW" and f.evidence["skipped"][0]["trade_id"] == "T2"


def test_unfavorable_complaint_adjustment_and_missing_approval():
    r = check("price_adjustment", adj(reason="customer_complaint", favorable_to_customer=False,
                                      principal_approval_documented=False))
    assert r.outcome == "VIOLATION" and len(r.findings) == 2


# ---------------- confirmations ---------------- #

def test_deadline_skips_weekend():
    friday = datetime(2026, 10, 23, 15, 0, tzinfo=UTC)
    p = P.resolve("confirmation", date(2026, 10, 23))
    assert confirmation_deadline(friday, p).date() == date(2026, 10, 26)  # Monday


def test_late_and_pending_confirmations():
    late = ConfirmationCheckRequest(trade_id="T1", executed_at=T0, as_of=T0 + timedelta(days=3),
                                    confirmation_sent_at=T0 + timedelta(days=2, hours=12))
    assert check("confirmation", late).outcome == "VIOLATION"
    pending = ConfirmationCheckRequest(trade_id="T1", executed_at=T0, as_of=T0 + timedelta(hours=2))
    r = check("confirmation", pending)
    assert r.outcome == "PASS" and r.findings[0].severity == "INFO"


# ---------------- funding ---------------- #

@pytest.mark.parametrize("method,outcome", [
    ("wire", "PASS"), ("credit_card", "VIOLATION"), ("credit_card_linked", "VIOLATION"),
    ("ach", "REVIEW"), ("other", "REVIEW"),
])
def test_funding_methods(method, outcome):
    req = DepositCheckRequest(deposit_id="D1", account_id="A1", amount_usd=Decimal("500"),
                              funding_method=method, received_at=T0)
    assert check("deposit", req).outcome == outcome


def test_parameter_versions_resolve_by_date():
    ps = ParameterSet(groups={"g": [
        {"version": "v1", "in_force_from": date(2025, 1, 1)},
        {"version": "v2", "in_force_from": date(2026, 6, 1)},
    ]})
    assert ps.resolve("g", date(2026, 1, 1))["version"] == "v1"
    assert ps.resolve("g", date(2026, 7, 1))["version"] == "v2"
