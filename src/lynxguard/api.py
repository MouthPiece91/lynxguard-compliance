"""HTTP API.

Flow: a dealer system submits an event -> rules run -> the result is written to
the organization's audit chain -> a reviewer records PROCEED or CANCEL, which is
appended as a separate audit record. The engine never executes or blocks a trade.

Run locally:
    LYNXGUARD_KEYS_FILE=keys.json uvicorn lynxguard.api:build_app --factory --reload
"""

from __future__ import annotations

import os
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from lynxguard import __version__
from lynxguard.audit import AuditLog
from lynxguard.auth import KeyStore, Principal
from lynxguard.core import run_rules
from lynxguard.regimes import forex_us
from lynxguard.regimes.forex_us.models import (
    ConfirmationCheckRequest,
    DepositCheckRequest,
    PriceAdjustmentCheckRequest,
    TradeCheckRequest,
)


class Decision(BaseModel):
    decision: Literal["PROCEED", "CANCEL"]
    reviewer: str = Field(min_length=1, description="Name or ID of the person deciding")
    reason: str = Field(min_length=1, description="Why - required for the audit trail")


def create_app(keys: KeyStore, audit: AuditLog) -> FastAPI:
    app = FastAPI(
        title="LynxGuard Compliance API",
        version=__version__,
        description="Rule-based compliance checks with citations and a tamper-evident audit trail.",
    )

    def principal(x_api_key: str | None = Header(default=None)) -> Principal:
        p = keys.authenticate(x_api_key)
        if p is None:
            raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")
        return p

    def _run(regime, event_type: str, request: BaseModel, who: Principal) -> dict:
        result = run_rules(event_type, request, regime.RULES, regime.load_parameters())
        payload = {
            "regime": regime.REGIME_ID,
            "event_type": event_type,
            "submitted_by_key": who.key_name,
            "request": request.model_dump(mode="json"),
            "outcome": result.outcome,
            "findings": [f.model_dump(mode="json") for f in result.findings],
            "rules_run": result.rules_run,
            "engine_version": __version__,
        }
        rec = audit.append(who.org_id, "check", payload)
        return {
            "check_id": rec["record_id"],
            "outcome": result.outcome,
            "findings": payload["findings"],
            "rules_run": result.rules_run,
            "audit_hash": rec["hash"],
            "requires_human_decision": result.outcome != "PASS",
        }

    @app.get("/health")
    def health():
        return {"status": "ok", "version": __version__}

    @app.get("/v1/rules")
    def list_rules():
        return [
            {
                "regime": forex_us.REGIME_ID,
                "rule_id": r.rule_id,
                "title": r.title,
                "event_type": r.event_type,
                "version": r.version,
                "citations": [c.model_dump() for c in r.citations],
            }
            for r in forex_us.RULES
        ]

    @app.post("/v1/forex-us/checks/trade")
    def check_trade(req: TradeCheckRequest, who: Principal = Depends(principal)):
        return _run(forex_us, "trade", req, who)

    @app.post("/v1/forex-us/checks/price-adjustment")
    def check_adjustment(req: PriceAdjustmentCheckRequest, who: Principal = Depends(principal)):
        return _run(forex_us, "price_adjustment", req, who)

    @app.post("/v1/forex-us/checks/confirmation")
    def check_confirmation(req: ConfirmationCheckRequest, who: Principal = Depends(principal)):
        return _run(forex_us, "confirmation", req, who)

    @app.post("/v1/forex-us/checks/deposit")
    def check_deposit(req: DepositCheckRequest, who: Principal = Depends(principal)):
        return _run(forex_us, "deposit", req, who)

    @app.get("/v1/checks/{check_id}")
    def get_check(check_id: str, who: Principal = Depends(principal)):
        rec = audit.get(who.org_id, check_id)  # org-scoped: other orgs' records are invisible
        if rec is None or rec["kind"] != "check":
            raise HTTPException(status_code=404, detail="Check not found")
        rec["decisions"] = audit.decisions_for(who.org_id, check_id)
        return rec

    @app.post("/v1/checks/{check_id}/decision")
    def decide(check_id: str, body: Decision, who: Principal = Depends(principal)):
        rec = audit.get(who.org_id, check_id)
        if rec is None or rec["kind"] != "check":
            raise HTTPException(status_code=404, detail="Check not found")
        out = audit.append(who.org_id, "decision", body.model_dump() | {"key": who.key_name},
                           ref_id=check_id)
        return {"decision_id": out["record_id"], "audit_hash": out["hash"]}

    @app.get("/v1/audit")
    def list_audit(limit: int = 100, who: Principal = Depends(principal)):
        return audit.list(who.org_id, min(max(limit, 1), 1000))

    @app.get("/v1/audit/verify")
    def verify_audit(who: Principal = Depends(principal)):
        return audit.verify(who.org_id)

    return app


def build_app() -> FastAPI:
    """App factory configured from environment variables (used by uvicorn --factory)."""
    keys_file = os.environ.get("LYNXGUARD_KEYS_FILE")
    keys = KeyStore.from_file(keys_file) if keys_file else KeyStore({})
    return create_app(keys, AuditLog(os.environ.get("LYNXGUARD_AUDIT_DB", "lynxguard_audit.db")))

