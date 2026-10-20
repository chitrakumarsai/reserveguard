"""HTTP API over a SettlementService (fake Airwallex until the REST client lands)."""

from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, NonNegativeInt

from reserveguard.airwallex.client import AirwallexError
from reserveguard.config import Settings, get_settings
from reserveguard.scenario import build_demo_service
from reserveguard.settlement import SettlementLine, SettlementService


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


def get_service(request: Request) -> SettlementService:
    service: SettlementService = request.app.state.service
    return service


ServiceDep = Annotated[SettlementService, Depends(get_service)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.use_fake_airwallex:
        raise RuntimeError(
            "RestAirwallexClient is not implemented yet; set USE_FAKE_AIRWALLEX=true"
        )
    app = FastAPI(title="ReserveGuard")
    app.state.service, _ = build_demo_service()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/sellers", response_model=list[SellerOut])
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

    # TODO: authenticate this money-moving endpoint before connecting the real client.
    @app.post("/settle", response_model=list[SettlementLine])
    def settle(body: SettleRequest, service: ServiceDep) -> list[SettlementLine]:
        try:
            return service.settle_cycle(body.gross_cents, cycle_id=body.cycle_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="unknown seller") from exc
        except AirwallexError as exc:
            raise HTTPException(status_code=502, detail="payment provider error") from exc

    @app.get("/reconciliation", response_model=list[CheckOut])
    def reconciliation(service: ServiceDep) -> list[CheckOut]:
        return [
            CheckOut(name=c.name, passed=c.passed, detail=c.detail)
            for c in service.reconciliation()
        ]

    return app


app = create_app()
