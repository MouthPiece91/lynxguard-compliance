import sqlite3

import pytest
from fastapi.testclient import TestClient

from lynxguard.api import create_app
from lynxguard.audit import AuditLog
from lynxguard.auth import KeyStore, hash_key

KEY_A = "lg_test_org_a_key"
KEY_B = "lg_test_org_b_key"

HEDGE = {
    "account": {
        "account_id": "A1",
        "security_deposit_usd": "100000",
        "open_lots": [{
            "lot_id": "L1", "pair": "EUR/USD", "side": "BUY", "quantity": "10000",
            "price": "1.08", "opened_at": "2026-10-20T13:00:00+00:00", "notional_usd": "10800",
        }],
    },
    "trade": {
        "trade_id": "T1", "pair": "EUR/USD", "side": "SELL", "quantity": "10000", "price": "1.09",
        "executed_at": "2026-10-20T14:00:00+00:00", "notional_usd": "10900", "position_effect": "open",
    },
}


@pytest.fixture
def audit():
    return AuditLog(":memory:")


@pytest.fixture
def client(audit):
    keys = KeyStore({
        hash_key(KEY_A): {"org_id": "org-a", "name": "desk-a"},
        hash_key(KEY_B): {"org_id": "org-b", "name": "desk-b"},
    })
    return TestClient(create_app(keys, audit))


def test_requires_api_key(client):
    assert client.post("/v1/forex-us/checks/trade", json=HEDGE).status_code == 401
    assert client.post("/v1/forex-us/checks/trade", json=HEDGE,
                       headers={"X-API-Key": "lg_wrong"}).status_code == 401


def test_rules_catalog_lists_citations(client):
    rules = client.get("/v1/rules").json()
    assert {r["rule_id"] for r in rules} >= {"FX-SD-001", "FX-FIFO-001", "FX-ADJ-001"}
    assert all(r["citations"] for r in rules)


def test_hedge_flagged_then_human_decision_recorded(client):
    h = {"X-API-Key": KEY_A}
    res = client.post("/v1/forex-us/checks/trade", json=HEDGE, headers=h).json()
    assert res["outcome"] == "VIOLATION" and res["requires_human_decision"]
    assert res["findings"][0]["citations"][0]["section"] == "2-43(b)"

    d = client.post(f"/v1/checks/{res['check_id']}/decision", headers=h,
                    json={"decision": "CANCEL", "reviewer": "F. Adusei", "reason": "Hedge not allowed"})
    assert d.status_code == 200
    rec = client.get(f"/v1/checks/{res['check_id']}", headers=h).json()
    assert rec["decisions"][0]["payload"]["decision"] == "CANCEL"
    assert client.get("/v1/audit/verify", headers=h).json() == {
        "valid": True, "records_checked": 2, "head": d.json()["audit_hash"]}


def test_orgs_cannot_see_each_other(client):
    res = client.post("/v1/forex-us/checks/trade", json=HEDGE, headers={"X-API-Key": KEY_A}).json()
    other = {"X-API-Key": KEY_B}
    assert client.get(f"/v1/checks/{res['check_id']}", headers=other).status_code == 404
    assert client.post(f"/v1/checks/{res['check_id']}/decision", headers=other,
                       json={"decision": "PROCEED", "reviewer": "x", "reason": "y"}).status_code == 404
    assert client.get("/v1/audit", headers=other).json() == []


def test_decision_requires_reason(client):
    h = {"X-API-Key": KEY_A}
    res = client.post("/v1/forex-us/checks/trade", json=HEDGE, headers=h).json()
    r = client.post(f"/v1/checks/{res['check_id']}/decision", headers=h,
                    json={"decision": "PROCEED", "reviewer": "x", "reason": ""})
    assert r.status_code == 422


def test_naive_timestamp_rejected(client):
    bad = {**HEDGE, "trade": {**HEDGE["trade"], "executed_at": "2026-10-20T14:00:00"}}
    r = client.post("/v1/forex-us/checks/trade", json=bad, headers={"X-API-Key": KEY_A})
    assert r.status_code == 422


def test_audit_log_is_append_only_and_tamper_evident(audit):
    audit.append("org-a", "check", {"n": 1})
    audit.append("org-a", "check", {"n": 2})
    with pytest.raises(sqlite3.DatabaseError):
        audit._db.execute("UPDATE audit_log SET payload='{}'")
    with pytest.raises(sqlite3.DatabaseError):
        audit._db.execute("DELETE FROM audit_log")
    # Simulate someone bypassing the triggers and editing a record directly.
    audit._db.execute("DROP TRIGGER audit_no_update")
    audit._db.execute("UPDATE audit_log SET payload='{\"n\":99}' WHERE seq=1")
    result = audit.verify("org-a")
    assert result["valid"] is False and result["records_checked"] == 0
