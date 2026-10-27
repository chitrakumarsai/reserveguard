"""HTTP API over a SettlementService.

Uses the fake Airwallex client unless USE_FAKE_AIRWALLEX=false, in which case it moves
real (sandbox) money for the sellers in data/sandbox_sellers.json. Every endpoint except
/health needs ``Authorization: Bearer <RESERVEGUARD_API_TOKEN>``.
"""

import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, NonNegativeInt

from reserveguard.agent.evidence import (
    MAX_EVIDENCE_TEXT,
    EvidenceItem,
    classify_evidence_with_source,
)
from reserveguard.airwallex.client import AirwallexError
from reserveguard.config import Settings, get_settings
from reserveguard.models import ReserveDecision, RiskSignal
from reserveguard.reserve_engine import AdjustmentPlan
from reserveguard.scenario import SANDBOX_SELLERS_PATH, build_service
from reserveguard.settlement import PaymentNotSettledError, SettlementLine, SettlementService

__all__ = ["ASSESS_RATE_LIMIT", "MAX_EVIDENCE_TEXT", "RateLimiter", "app", "create_app"]

MAX_EVIDENCE_ITEMS = 100
# /assess may call Claude, so it is capped per process: this many calls per window.
ASSESS_RATE_LIMIT = 10
ASSESS_RATE_WINDOW_S = 60.0


class SettleRequest(BaseModel):
    cycle_id: str = Field(min_length=1, max_length=64)
    gross_cents: dict[str, NonNegativeInt]


class SellerOut(BaseModel):
    seller_id: str
    name: str
    tier: str
    rate_bps: int
    held_cents: int
    debt_cents: int


class CheckOut(BaseModel):
    name: str
    passed: bool
    detail: str


class AssessRequest(BaseModel):
    evidence: list[EvidenceItem] = Field(min_length=1, max_length=MAX_EVIDENCE_ITEMS)


class AssessOut(BaseModel):
    decision: ReserveDecision
    signals: list[RiskSignal]
    # Set when the change was applied at once; None while it waits for /approve.
    memo: str | None
    classifier: str  # "claude" or "keywords" (also used when Claude fails)


class ApproveOut(BaseModel):
    decision: ReserveDecision
    plan: AdjustmentPlan
    memo: str


def get_service(request: Request) -> SettlementService:
    service: SettlementService = request.app.state.service
    return service


ServiceDep = Annotated[SettlementService, Depends(get_service)]
_bearer = HTTPBearer(auto_error=False)


def require_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> None:
    """Fail closed: no configured token means no access, not open access."""
    expected: str = request.app.state.api_token
    if not expected:
        raise HTTPException(status_code=503, detail="API token not configured")
    given = credentials.credentials if credentials else ""
    if not secrets.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(
            status_code=401, detail="invalid token", headers={"WWW-Authenticate": "Bearer"}
        )


Authorized = [Depends(require_token)]


class RateLimiter:
    """Sliding-window limit on calls, shared by every caller of this process."""

    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window_s = window_s
        self._clock = clock
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self) -> None:
        with self._lock:
            now = self._clock()
            while self._calls and now - self._calls[0] >= self._window_s:
                self._calls.popleft()
            if len(self._calls) >= self._limit:
                raise HTTPException(status_code=429, detail="too many assessments; retry later")
            self._calls.append(now)


def require_sellers(service: SettlementService, *seller_ids: str) -> None:
    known = {s.seller_id for s in service.sellers}
    if any(seller_id not in known for seller_id in seller_ids):
        raise HTTPException(status_code=404, detail="unknown seller")


def provider_failure(exc: AirwallexError) -> HTTPException:
    """502 that tells the caller whether money moved, so it knows if a retry is safe."""
    if isinstance(exc, PaymentNotSettledError):
        return HTTPException(
            status_code=502,
            detail={
                "error": "payment_not_settled",
                "payment_id": exc.payment_id,
                "status": exc.status,
                "ledger_applied": exc.ledger_applied,
            },
        )
    return HTTPException(status_code=502, detail="payment provider error")


def create_app(
    settings: Settings | None = None, sellers_path: Path = SANDBOX_SELLERS_PATH
) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="ReserveGuard")
    app.state.service, _ = build_service(settings, sellers_path)
    assess_limit = RateLimiter(ASSESS_RATE_LIMIT, ASSESS_RATE_WINDOW_S)
    app.state.api_token = settings.reserveguard_api_token.get_secret_value()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/sellers", response_model=list[SellerOut], dependencies=Authorized)
    def sellers(service: ServiceDep) -> list[SellerOut]:
        out = []
        for seller in service.sellers:
            totals = service.ledger.totals(seller.seller_id)
            out.append(
                SellerOut(
                    seller_id=seller.seller_id,
                    name=seller.name,
                    tier=seller.tier.value,
                    rate_bps=service.current_rate_bps(seller.seller_id),
                    held_cents=totals.held_cents,
                    debt_cents=totals.debt_cents,
                )
            )
        return out

    @app.post("/settle", response_model=list[SettlementLine], dependencies=Authorized)
    def settle(body: SettleRequest, service: ServiceDep) -> list[SettlementLine]:
        """Returns once every payout of the cycle has SETTLED; replaying the cycle re-checks
        payouts that were still pending and never pays a seller twice."""
        require_sellers(service, *body.gross_cents)
        try:
            return service.settle_cycle(body.gross_cents, cycle_id=body.cycle_id)
        except AirwallexError as exc:
            raise provider_failure(exc) from exc

    @app.post("/sellers/{seller_id}/assess", response_model=AssessOut, dependencies=Authorized)
    def assess(seller_id: str, body: AssessRequest, service: ServiceDep) -> AssessOut:
        """Re-rate ONE seller. The LLM only names signals; weights and amounts are fixed.
        Evidence alone never releases money: any decrease also waits for /approve."""
        require_sellers(service, seller_id)
        assess_limit.check()
        signals, classifier = classify_evidence_with_source(body.evidence, settings)
        try:
            decision, memo = service.assess_detailed(seller_id, signals, hold_releases=True)
        except AirwallexError as exc:
            raise provider_failure(exc) from exc
        return AssessOut(decision=decision, signals=signals, memo=memo, classifier=classifier)

    @app.post("/sellers/{seller_id}/approve", response_model=ApproveOut, dependencies=Authorized)
    def approve(seller_id: str, service: ServiceDep) -> ApproveOut:
        require_sellers(service, seller_id)
        try:
            result = service.approve(seller_id)
        except LookupError as exc:
            raise HTTPException(status_code=409, detail="no pending decision") from exc
        except AirwallexError as exc:
            raise provider_failure(exc) from exc
        return ApproveOut(decision=result.decision, plan=result.plan, memo=result.memo)

    @app.get("/reconciliation", response_model=list[CheckOut], dependencies=Authorized)
    def reconciliation(service: ServiceDep) -> list[CheckOut]:
        return [
            CheckOut(name=c.name, passed=c.passed, detail=c.detail)
            for c in service.reconciliation()
        ]

    return app


app = create_app()
