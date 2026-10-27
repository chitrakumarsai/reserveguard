"""Turn unstructured evidence into RiskSignals. The LLM picks kinds; weights stay deterministic."""

import json
import logging
import re
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

import anthropic
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from reserveguard.config import Settings, get_settings
from reserveguard.models import RiskKind, RiskSignal

logger = logging.getLogger(__name__)

CLUSTER_MIN_COMPLAINTS = 3
SIGNAL_WEIGHTS_BPS: dict[RiskKind, int] = {
    RiskKind.COMPLAINT: 500,
    RiskKind.COMPLAINT_CLUSTER: 1_500,
    RiskKind.TRACKING_FAILURE: 500,
    RiskKind.DELIVERY_CONFIRMED: 0,
}
_CLEARS: dict[RiskKind, RiskKind] = {RiskKind.DELIVERY_CONFIRMED: RiskKind.COMPLAINT_CLUSTER}

_COMPLAINT_WORDS = ("never arrived", "never received", "not received", "did not arrive")
_FAILURE_WORDS = ("delivery failed", "failed delivery", "undeliverable", "returned to sender")
_DELIVERED_WORDS = ("delivered", "delivery confirmed", "signed for")
_NEGATED_DELIVERY = re.compile(r"\b(not|never|un)[\s-]?deliver", re.IGNORECASE)
LLM_TIMEOUT_SECONDS = 30.0

_SYSTEM_PROMPT = (
    "You classify marketplace evidence for one seller into risk signals. "
    f"Allowed kinds: {', '.join(k.value for k in RiskKind)}. "
    "Group three or more 'item never arrived' complaints into one complaint_cluster. "
    "Each signal must cite the ids of the evidence items it is based on. "
    "Do not output money amounts or rates."
)


# Caps on caller-supplied evidence, which is sent to Claude when USE_LLM is on.
MAX_EVIDENCE_ID = 64
MAX_EVIDENCE_TEXT = 2000


class EvidenceItem(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, max_length=MAX_EVIDENCE_ID)
    source: str = Field(min_length=1, max_length=MAX_EVIDENCE_ID)
    timestamp: datetime
    text: str = Field(max_length=MAX_EVIDENCE_TEXT)


class _LLMSignal(BaseModel):
    kind: RiskKind
    evidence_ids: list[str]
    summary: str


class _LLMResponse(BaseModel):
    signals: list[_LLMSignal]


def load_evidence(path: Path) -> list[EvidenceItem]:
    return TypeAdapter(list[EvidenceItem]).validate_json(path.read_bytes())


def _signal(kind: RiskKind, ids: Sequence[str], summary: str) -> RiskSignal:
    return RiskSignal(
        kind=kind,
        weight_bps=SIGNAL_WEIGHTS_BPS[kind],
        evidence_ids=tuple(ids),
        clears=_CLEARS.get(kind),
        summary=summary,
    )


def _matches(text: str, words: Sequence[str]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def _is_delivered(text: str) -> bool:
    return _matches(text, _DELIVERED_WORDS) and not _NEGATED_DELIVERY.search(text)


def classify_with_keywords(items: Sequence[EvidenceItem]) -> list[RiskSignal]:
    complaints = [i.id for i in items if _matches(i.text, _COMPLAINT_WORDS)]
    rest = [i for i in items if i.id not in complaints]
    failures = [i.id for i in rest if _matches(i.text, _FAILURE_WORDS)]
    delivered = [i.id for i in rest if i.id not in failures and _is_delivered(i.text)]

    signals: list[RiskSignal] = []
    if len(complaints) >= CLUSTER_MIN_COMPLAINTS:
        summary = f"{len(complaints)} item-never-arrived complaints"
        signals.append(_signal(RiskKind.COMPLAINT_CLUSTER, complaints, summary))
    else:
        signals.extend(_signal(RiskKind.COMPLAINT, [c], "item never arrived") for c in complaints)
    signals.extend(_signal(RiskKind.TRACKING_FAILURE, [f], "carrier failure") for f in failures)
    if delivered:
        signals.append(
            _signal(RiskKind.DELIVERY_CONFIRMED, delivered, "carrier confirmed delivery")
        )
    return signals


def _call_claude(items: Sequence[EvidenceItem], settings: Settings) -> _LLMResponse:
    api_key = settings.anthropic_api_key.get_secret_value() or None
    client = anthropic.Anthropic(api_key=api_key, timeout=LLM_TIMEOUT_SECONDS, max_retries=2)
    payload = json.dumps([i.model_dump(mode="json") for i in items])
    response = client.messages.parse(
        model=settings.llm_model,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
        output_format=_LLMResponse,
    )
    if response.parsed_output is None:
        raise ValueError(f"Claude returned no parsable output (stop={response.stop_reason})")
    return response.parsed_output


def _check_llm_signal(raw: _LLMSignal, by_id: dict[str, EvidenceItem]) -> None:
    """Evidence text is untrusted, so the LLM's claims are re-checked deterministically."""
    unknown = set(raw.evidence_ids) - by_id.keys()
    if unknown or not raw.evidence_ids:
        raise ValueError(f"LLM cited unknown or no evidence ids: {sorted(unknown)}")
    if (
        raw.kind == RiskKind.COMPLAINT_CLUSTER
        and len(set(raw.evidence_ids)) < CLUSTER_MIN_COMPLAINTS
    ):
        raise ValueError("LLM complaint_cluster cites too few items")
    if raw.kind == RiskKind.DELIVERY_CONFIRMED and not all(
        _is_delivered(by_id[i].text) for i in raw.evidence_ids
    ):
        raise ValueError("LLM delivery_confirmed not backed by delivery evidence")


def classify_with_llm(items: Sequence[EvidenceItem], settings: Settings) -> list[RiskSignal]:
    by_id = {i.id: i for i in items}
    result = _call_claude(items, settings)
    for raw in result.signals:
        _check_llm_signal(raw, by_id)
    return [_signal(raw.kind, raw.evidence_ids, raw.summary) for raw in result.signals]


def classify_evidence(
    items: Sequence[EvidenceItem], settings: Settings | None = None
) -> list[RiskSignal]:
    return classify_evidence_with_source(items, settings)[0]


def classify_evidence_with_source(
    items: Sequence[EvidenceItem], settings: Settings | None = None
) -> tuple[list[RiskSignal], str]:
    """Signals plus which classifier produced them: "claude" or "keywords"."""
    settings = settings or get_settings()
    if not settings.use_llm:
        return classify_with_keywords(items), "keywords"
    try:
        return classify_with_llm(items, settings), "claude"
    except Exception:
        logger.exception("LLM evidence classification failed; using keyword rule")
        return classify_with_keywords(items), "keywords"
