"""The service confirms each payment before trusting it: failed ones are never recorded,
pending ones are recorded and re-checked, and one deadline bounds each command."""

from collections.abc import Iterator

import pytest

from reserveguard.agent.evidence import classify_evidence, load_evidence
from reserveguard.airwallex.client import AirwallexTransientError
from reserveguard.airwallex.fake import FakeAirwallexClient
from reserveguard.config import Settings
from reserveguard.models import EntryKind
from reserveguard.scenario import (
    DELIVERY_CONFIRMATIONS,
    DEMO_GROSS_CENTS,
    DEMO_SELLERS,
    SAMPLE_EVIDENCE_PATH,
)
from reserveguard.settlement import (
    MAX_ATTEMPTS,
    PAYMENT_WAIT_BUDGET_S,
    PaymentNotSettledError,
    SettlementService,
)

pytestmark = pytest.mark.unit

OFFLINE = Settings(use_llm=False)
RISK = classify_evidence(load_evidence(SAMPLE_EVIDENCE_PATH), OFFLINE)
DELIVERED = classify_evidence(DELIVERY_CONFIRMATIONS, OFFLINE)


class ScriptedWait:
    """Returns the scripted statuses in order (then SETTLED); records every call."""

    def __init__(self, *statuses: str) -> None:
        self._statuses: Iterator[str] = iter(statuses)
        self.calls: list[tuple[str, float]] = []

    def __call__(self, object_id: str, timeout_s: float) -> str:
        self.calls.append((object_id, timeout_s))
        return next(self._statuses, "SETTLED")


def _service(
    wait: ScriptedWait, clock: list[float] | None = None
) -> tuple[SettlementService, FakeAirwallexClient]:
    fake = FakeAirwallexClient({None: sum(DEMO_GROSS_CENTS.values())})
    ticks = iter(clock or [])
    service = SettlementService(
        fake, DEMO_SELLERS, await_payment=wait, clock=lambda: next(ticks, 0.0)
    )
    return service, fake


def _payouts(service: SettlementService, seller_id: str) -> int:
    return sum(
        e.amount_cents
        for e in service.ledger.entries
        if e.seller_id == seller_id and e.kind is EntryKind.PAYOUT
    )


def test_failed_payout_is_not_recorded_and_the_cycle_can_be_retried() -> None:
    service, fake = _service(ScriptedWait("FAILED"))

    with pytest.raises(PaymentNotSettledError) as failed:
        service.settle_cycle({"A": 300_000}, cycle_id="c1")

    assert failed.value.status == "FAILED"
    assert failed.value.ledger_applied is False
    assert service.ledger.entries == ()
    assert service.settle_cycle({"A": 300_000}, cycle_id="c1")[0].payout_cents == 255_000
    assert _payouts(service, "A") == 255_000
    # The retry sends a new key: the old one would only find the FAILED transfer again.
    assert len(fake.calls) == 2 and fake.calls[0] != fake.calls[1]


def test_pending_payout_is_recorded_and_rechecked_on_replay() -> None:
    wait = ScriptedWait("PENDING", "PENDING", "SETTLED")
    service, fake = _service(wait)

    with pytest.raises(PaymentNotSettledError) as pending:
        service.settle_cycle({"A": 300_000}, cycle_id="c1")
    assert pending.value.ledger_applied is True
    with pytest.raises(PaymentNotSettledError):  # replay: still pending, still an error
        service.settle_cycle({"A": 300_000}, cycle_id="c1")
    lines = service.settle_cycle({"A": 300_000}, cycle_id="c1")  # now settled

    assert lines[0].transfer_id == pending.value.payment_id
    assert [c[0] for c in wait.calls] == [pending.value.payment_id] * 3
    assert _payouts(service, "A") == 255_000  # paid once
    assert fake.get_balance("acct_seller_a") == 255_000


def test_wait_timeout_counts_as_pending() -> None:
    def wait(_object_id: str, _timeout_s: float) -> str:
        raise AirwallexTransientError("still PENDING after 60s")

    fake = FakeAirwallexClient({None: 1_000_000})
    service = SettlementService(fake, DEMO_SELLERS, await_payment=wait)

    with pytest.raises(PaymentNotSettledError) as pending:
        service.settle_cycle({"A": 300_000}, cycle_id="c1")
    assert pending.value.status == "PENDING"
    assert pending.value.ledger_applied is True


def test_one_deadline_bounds_every_wait_in_a_command() -> None:
    wait = ScriptedWait()
    service, _ = _service(wait, clock=[0.0, 0.0, 25.0, 70.0])

    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")

    budgets = [timeout for _, timeout in wait.calls]
    assert budgets == [PAYMENT_WAIT_BUDGET_S, PAYMENT_WAIT_BUDGET_S - 25.0, 0.0]


def test_failed_charge_keeps_the_decision_pending() -> None:
    wait = ScriptedWait()
    service, _ = _service(wait)
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    service.assess("C", RISK)
    wait._statuses = iter(["CANCELLED"])

    with pytest.raises(PaymentNotSettledError) as failed:
        service.approve("C")

    assert failed.value.ledger_applied is False
    assert service.current_rate_bps("C") == 1500
    # The fake moves money even for a scripted CANCELLED, so only the ledger is checked.
    assert service.approve("C").plan.charge_cents == 80_000  # retry succeeds


def test_charge_retried_after_timeouts_reuses_its_key() -> None:
    service, fake = _service(ScriptedWait())
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    service.assess("C", RISK)
    fake.fail_next(MAX_ATTEMPTS)

    with pytest.raises(AirwallexTransientError):
        service.approve("C")  # outcome unknown: Airwallex may have made the charge
    service.approve("C")

    charge_keys = fake.calls[-(MAX_ATTEMPTS + 1) :]
    assert len(set(charge_keys)) == 1  # so Airwallex dedupes instead of charging twice


def test_assess_detailed_returns_the_memo_of_an_applied_change() -> None:
    service, _ = _service(ScriptedWait())
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    service.assess("C", RISK)
    service.approve("C")

    decision, memo = service.assess_detailed("C", DELIVERED)

    assert decision.needs_approval is False
    assert memo is not None and "Auto-applied" in memo


def test_hold_releases_sends_a_decrease_to_approval() -> None:
    service, fake = _service(ScriptedWait())
    service.settle_cycle(DEMO_GROSS_CENTS, cycle_id="c1")
    service.assess("C", RISK)
    service.approve("C")
    wallet = fake.get_balance("acct_seller_c")

    decision, memo = service.assess_detailed("C", DELIVERED, hold_releases=True)

    assert decision.needs_approval is True and memo is None
    assert fake.get_balance("acct_seller_c") == wallet  # nothing released yet
    assert service.approve("C").plan.release_cents == 60_000
