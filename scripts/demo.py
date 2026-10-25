"""Run six sample situations through LynxGuard and print what it finds.

    python scripts/demo.py

Nothing is saved: the demo uses a throwaway in-memory audit log and a demo key.
Use it to show the system in a meeting or to check it still works after a change.
"""

from fastapi.testclient import TestClient

from lynxguard.api import create_app
from lynxguard.audit import AuditLog
from lynxguard.auth import KeyStore, hash_key

KEY = "lg_demo_key"
client = TestClient(create_app(
    KeyStore({hash_key(KEY): {"org_id": "demo-dealer", "name": "compliance-desk"}}),
    AuditLog(":memory:"),
))
H = {"X-API-Key": KEY}


def lot(lot_id, side, qty, opened_at, pair="EUR/USD"):
    return {"lot_id": lot_id, "pair": pair, "side": side, "quantity": qty, "price": "1.08",
            "opened_at": opened_at, "notional_usd": qty}


def trade(open_lots, deposit, **t):
    base = {"trade_id": "T1", "pair": "EUR/USD", "price": "1.08",
            "executed_at": "2026-10-20T14:00:00+00:00"}
    base.update(t)
    base.setdefault("notional_usd", base["quantity"])
    return {"account": {"account_id": "A1", "security_deposit_usd": deposit, "open_lots": open_lots},
            "trade": base}


def show(n, title, response):
    body = response.json()
    print(f"\n{n}. {title}\n   RESULT: {body['outcome']}")
    for f in body["findings"]:
        c = f["citations"][0]
        print(f"   - {f['message']}\n     Rule: {c['source']} {c['section']}")
    return body


show(1, "Buy 100,000 EUR/USD with $5,000 on deposit",
     client.post("/v1/forex-us/checks/trade", headers=H,
                 json=trade([], "5000", side="BUY", quantity="100000", position_effect="open")))
show(2, "Buy 100,000 USD/MXN with only $2,000 on deposit",
     client.post("/v1/forex-us/checks/trade", headers=H,
                 json=trade([], "2000", pair="USD/MXN", side="BUY", quantity="100000", position_effect="open")))
hedge = show(3, "Open a SELL on EUR/USD while a BUY is still open",
             client.post("/v1/forex-us/checks/trade", headers=H,
                         json=trade([lot("L1", "BUY", "10000", "2026-10-20T13:00:00+00:00")], "100000",
                                    side="SELL", quantity="10000", position_effect="open")))
show(4, "Close the NEWER of two buy lots instead of the older one",
     client.post("/v1/forex-us/checks/trade", headers=H,
                 json=trade([lot("OLD", "BUY", "10000", "2026-10-20T12:00:00+00:00"),
                             lot("NEW", "BUY", "10000", "2026-10-20T13:50:00+00:00")], "100000",
                            side="SELL", quantity="10000", position_effect="close", closes_lot_ids=["NEW"])))
show(5, "Customer funds $500 by credit card",
     client.post("/v1/forex-us/checks/deposit", headers=H,
                 json={"deposit_id": "D1", "account_id": "A1", "amount_usd": "500",
                       "funding_method": "credit_card", "received_at": "2026-10-20T14:00:00Z"}))
show(6, "Trade repriced; customer told 16 minutes after the fill",
     client.post("/v1/forex-us/checks/price-adjustment", headers=H, json={
         "adjustment_id": "ADJ1", "trade_id": "T1", "pair": "EUR/USD",
         "executed_at": "2026-10-20T14:00:00Z", "adjusted_at": "2026-10-20T14:05:00Z",
         "reason": "counterparty_stp", "favorable_to_customer": False,
         "customer_notified_at": "2026-10-20T14:16:00Z", "counterparty_documentation_provided": True,
         "dealer_uses_stp_exclusively": True, "counterparty_unaffiliated": True,
         "principal_approval_documented": True,
         "window_trades": [{"trade_id": "T1", "side": "BUY"}, {"trade_id": "T2", "side": "SELL"}],
         "adjusted_trade_ids": ["T1", "T2"]}))

client.post(f"/v1/checks/{hedge['check_id']}/decision", headers=H,
            json={"decision": "CANCEL", "reviewer": "F. Adusei",
                  "reason": "Hedging not permitted for US retail accounts"})
print("\nReviewer decision on case 3: CANCEL by F. Adusei (recorded)")
v = client.get("/v1/audit/verify", headers=H).json()
print(f"Audit log check: valid={v['valid']}, records checked={v['records_checked']}")
