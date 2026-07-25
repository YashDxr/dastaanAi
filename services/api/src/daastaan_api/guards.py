"""Rate limiting, budget enforcement, and the audit trail.

Rate limit state lives in the database rather than process memory so it survives
an API restart and holds across replicas. Both guards are also re-checked inside
the worker, because a task can retry long after the request that created it.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from daastaan_common.models import AdminSetting, AuditLog, CostLedger, RateLimitEvent
from daastaan_contracts import limits
from fastapi import HTTPException, status
from sqlmodel import Session, func, select

log = structlog.get_logger(__name__)

BUDGET_CAP_KEY = "budget_cap_usd"


def enforce_rate_limit(session: Session, user_id: str, action: str, max_events: int) -> None:
    window_start = datetime.now(UTC) - timedelta(seconds=limits.RATE_LIMIT_WINDOW_SECONDS)
    used = session.exec(
        select(func.count())
        .select_from(RateLimitEvent)
        .where(
            RateLimitEvent.user_id == user_id,
            RateLimitEvent.action == action,
            RateLimitEvent.created_at >= window_start,
        )
    ).one()

    if used >= max_events:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"rate limit reached for {action}: {max_events} per "
            f"{limits.RATE_LIMIT_WINDOW_SECONDS // 60} minutes",
        )
    session.add(RateLimitEvent(user_id=user_id, action=action))


def total_spend_usd(session: Session) -> float:
    return float(session.exec(select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0))).one())


def budget_cap_usd(session: Session) -> float:
    setting = session.get(AdminSetting, BUDGET_CAP_KEY)
    if setting and isinstance(setting.value_json.get("amount"), int | float):
        return float(setting.value_json["amount"])
    return limits.DEFAULT_BUDGET_CAP_USD


def enforce_budget(session: Session) -> None:
    spent, cap = total_spend_usd(session), budget_cap_usd(session)
    if spent >= cap:
        raise HTTPException(
            status.HTTP_402_PAYMENT_REQUIRED,
            f"generation disabled: spend ${spent:.2f} has reached the ${cap:.2f} cap",
        )


def audit(
    session: Session,
    *,
    actor_user_id: str | None,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            metadata_json=metadata,
        )
    )
