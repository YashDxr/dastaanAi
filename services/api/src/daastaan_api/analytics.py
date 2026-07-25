"""Read-only rollups for the admin console.

A "run" here is one `StoryVersion` execution. There is no dedicated runs table
worth reading - `pipeline_runs` was never written to - and deriving runs from
`jobs` and `cost_ledger` instead means the console reports on stories that were
generated before any of this existed, rather than starting from an empty list.

Everything is assembled with a small fixed number of queries and stitched in
Python. Doing it as one wide join would need aggregate subqueries against three
tables and read far worse for no measurable gain at this scale.
"""

from collections import defaultdict
from datetime import datetime
from typing import Any

from daastaan_common.models import (
    CostLedger,
    Feedback,
    Job,
    MediaAsset,
    Story,
    StoryVersion,
    User,
)
from daastaan_contracts import JobStatus
from sqlalchemy import case
from sqlmodel import Session, col, func, select

MAX_RUNS = 100

# Postgres will not SUM a boolean, so cache hits are counted as 1/0.
_CACHE_HIT_COUNT = func.coalesce(func.sum(case((col(CostLedger.cache_hit), 1), else_=0)), 0)


def _duration_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    return max(int((end - start).total_seconds() * 1000), 0)


def _derive_status(statuses: list[str]) -> str:
    """A run is only as good as its worst stage."""
    if not statuses:
        return "pending"
    if JobStatus.FAILED in statuses:
        return "failed"
    if JobStatus.RUNNING in statuses:
        return "running"
    if all(s == JobStatus.SUCCEEDED for s in statuses):
        return "succeeded"
    return "running"


def _cost_rows_by_version(session: Session, version_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not version_ids:
        return {}
    rows = session.exec(
        select(
            CostLedger.version_id,
            func.count(CostLedger.id),
            func.coalesce(func.sum(CostLedger.cost_usd), 0.0),
            func.coalesce(func.sum(CostLedger.input_tokens), 0),
            func.coalesce(func.sum(CostLedger.output_tokens), 0),
            _CACHE_HIT_COUNT,
        )
        .where(col(CostLedger.version_id).in_(version_ids))
        .group_by(col(CostLedger.version_id))
    ).all()
    return {
        str(version_id): {
            "calls": int(calls),
            "cost_usd": round(float(cost), 6),
            "input_tokens": int(tin),
            "output_tokens": int(tout),
            "cache_hits": int(hits),
        }
        for version_id, calls, cost, tin, tout, hits in rows
    }


def _jobs_by_version(session: Session, version_ids: list[str]) -> dict[str, list[Job]]:
    if not version_ids:
        return {}
    jobs = session.exec(
        select(Job)
        .where(col(Job.version_id).in_(version_ids))
        .order_by(col(Job.created_at))
    ).all()
    grouped: dict[str, list[Job]] = defaultdict(list)
    for job in jobs:
        grouped[job.version_id].append(job)
    return grouped


def _regen_of(version: StoryVersion) -> dict[str, Any] | None:
    regen = (version.state_json or {}).get("regen")
    return regen if isinstance(regen, dict) else None


def run_summaries(
    session: Session, *, limit: int = MAX_RUNS, failed_only: bool = False
) -> list[dict[str, Any]]:
    versions = list(
        session.exec(
            select(StoryVersion)
            .order_by(col(StoryVersion.created_at).desc())
            .limit(min(limit, MAX_RUNS))
        ).all()
    )
    if not versions:
        return []

    version_ids = [v.id for v in versions]
    jobs = _jobs_by_version(session, version_ids)
    costs = _cost_rows_by_version(session, version_ids)

    story_ids = list({v.story_id for v in versions})
    stories = {
        s.id: s
        for s in session.exec(select(Story).where(col(Story.id).in_(story_ids))).all()
    }
    user_ids = list({s.user_id for s in stories.values()})
    users = {
        u.id: u for u in session.exec(select(User).where(col(User.id).in_(user_ids))).all()
    }

    summaries = []
    for version in versions:
        version_jobs = jobs.get(version.id, [])
        status = _derive_status([job.status for job in version_jobs])
        if failed_only and status != "failed":
            continue

        started = min((j.started_at for j in version_jobs if j.started_at), default=None)
        finished = (
            max((j.finished_at for j in version_jobs if j.finished_at), default=None)
            if status not in {"running", "pending"}
            else None
        )
        regen = _regen_of(version)
        story = stories.get(version.story_id)
        user = users.get(story.user_id) if story else None
        cost = costs.get(version.id, {})

        summaries.append(
            {
                "version_id": version.id,
                "story_id": version.story_id,
                "story_title": story.title if story else None,
                "user_id": story.user_id if story else None,
                "user_email": user.email if user else None,
                "version_number": version.version_number,
                "genre": version.genre,
                "mood": version.mood,
                "is_regen": regen is not None,
                "regen_scope": (regen or {}).get("scope"),
                "regen_stage": (regen or {}).get("target_stage"),
                "status": status,
                "created_at": version.created_at,
                "started_at": started,
                "finished_at": finished,
                "duration_ms": _duration_ms(started, finished),
                "stage_count": len(version_jobs),
                "calls": cost.get("calls", 0),
                "cost_usd": cost.get("cost_usd", 0.0),
                "input_tokens": cost.get("input_tokens", 0),
                "output_tokens": cost.get("output_tokens", 0),
                "cache_hits": cost.get("cache_hits", 0),
                "error": next((j.error for j in version_jobs if j.error), None),
            }
        )
    return summaries


def user_summaries(session: Session) -> list[dict[str, Any]]:
    """Every account with what it has cost.

    `cost_ledger.user_id` is stamped by the gateway on each paid call, so spend
    attributes to a person without needing to walk back through stories.
    """
    users = list(session.exec(select(User).order_by(col(User.created_at))).all())

    spend = {
        str(user_id): {
            "calls": int(calls),
            "cost_usd": round(float(cost), 6),
            "input_tokens": int(tin),
            "output_tokens": int(tout),
            "cache_hits": int(hits),
            "last_active_at": last,
        }
        for user_id, calls, cost, tin, tout, hits, last in session.exec(
            select(
                CostLedger.user_id,
                func.count(CostLedger.id),
                func.coalesce(func.sum(CostLedger.cost_usd), 0.0),
                func.coalesce(func.sum(CostLedger.input_tokens), 0),
                func.coalesce(func.sum(CostLedger.output_tokens), 0),
                _CACHE_HIT_COUNT,
                func.max(CostLedger.created_at),
            ).group_by(col(CostLedger.user_id))
        ).all()
        if user_id is not None
    }

    story_counts = {
        str(user_id): int(count)
        for user_id, count in session.exec(
            select(Story.user_id, func.count(Story.id)).group_by(col(Story.user_id))
        ).all()
    }

    return [
        {
            "id": user.id,
            "email": user.email,
            "role": user.role,
            "created_at": user.created_at,
            "stories": story_counts.get(user.id, 0),
            "calls": spend.get(user.id, {}).get("calls", 0),
            "cost_usd": spend.get(user.id, {}).get("cost_usd", 0.0),
            "input_tokens": spend.get(user.id, {}).get("input_tokens", 0),
            "output_tokens": spend.get(user.id, {}).get("output_tokens", 0),
            "cache_hits": spend.get(user.id, {}).get("cache_hits", 0),
            "last_active_at": spend.get(user.id, {}).get("last_active_at"),
        }
        for user in users
    ]


def user_cost_detail(session: Session, user_id: str) -> dict[str, Any] | None:
    user = session.get(User, user_id)
    if user is None:
        return None

    summary = next((u for u in user_summaries(session) if u["id"] == user_id), None)
    ledger = list(session.exec(select(CostLedger).where(CostLedger.user_id == user_id)).all())

    daily: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost_usd": 0.0, "calls": 0})
    by_stage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0}
    )
    by_model: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0}
    )
    by_version: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost_usd": 0.0, "calls": 0})

    for row in ledger:
        day = daily[row.created_at.date().isoformat()]
        day["cost_usd"] += row.cost_usd
        day["calls"] += 1
        for bucket in (by_stage[row.stage], by_model[row.model]):
            bucket["cost_usd"] += row.cost_usd
            bucket["calls"] += 1
            bucket["input_tokens"] += row.input_tokens
            bucket["output_tokens"] += row.output_tokens
        if row.version_id:
            by_version[row.version_id]["cost_usd"] += row.cost_usd
            by_version[row.version_id]["calls"] += 1

    # Spend is recorded against a version; the user thinks in stories, so roll
    # every version of a story back up into one line.
    by_story: dict[str, dict[str, Any]] = defaultdict(lambda: {"cost_usd": 0.0, "calls": 0})
    if by_version:
        versions = session.exec(
            select(StoryVersion).where(col(StoryVersion.id).in_(list(by_version)))
        ).all()
        titles = {
            s.id: s.title
            for s in session.exec(
                select(Story).where(col(Story.id).in_([v.story_id for v in versions]))
            ).all()
        }
        for version in versions:
            entry = by_story[version.story_id]
            entry["cost_usd"] += by_version[version.id]["cost_usd"]
            entry["calls"] += by_version[version.id]["calls"]
            entry["title"] = titles.get(version.story_id)

    return {
        "user": summary,
        "daily": [
            {"day": day, "cost_usd": round(v["cost_usd"], 6), "calls": v["calls"]}
            for day, v in sorted(daily.items())
        ],
        "by_stage": [
            {"label": label, "calls": v["calls"], "input_tokens": v["input_tokens"],
             "output_tokens": v["output_tokens"], "cost_usd": round(v["cost_usd"], 6)}
            for label, v in sorted(by_stage.items(), key=lambda kv: -kv[1]["cost_usd"])
        ],
        "by_model": [
            {"label": label, "calls": v["calls"], "input_tokens": v["input_tokens"],
             "output_tokens": v["output_tokens"], "cost_usd": round(v["cost_usd"], 6)}
            for label, v in sorted(by_model.items(), key=lambda kv: -kv[1]["cost_usd"])
        ],
        "by_story": [
            {"story_id": story_id, "title": v.get("title"),
             "cost_usd": round(v["cost_usd"], 6), "calls": v["calls"]}
            for story_id, v in sorted(by_story.items(), key=lambda kv: -kv[1]["cost_usd"])
        ],
        "cache_savings_usd": round(estimate_cache_savings(session, user_id=user_id), 6),
    }


def estimate_cache_savings(session: Session, *, user_id: str | None = None) -> float:
    """What the cache hits would have cost had they been real calls.

    A hit writes a zero-cost row, so the saving has to be inferred from the mean
    price of the paid calls for that same stage and model. Approximate by
    construction, and it is labelled as an estimate wherever it is displayed.
    """
    query = select(
        CostLedger.stage,
        CostLedger.model,
        col(CostLedger.cache_hit),
        func.count(CostLedger.id),
        func.coalesce(func.sum(CostLedger.cost_usd), 0.0),
    ).group_by(col(CostLedger.stage), col(CostLedger.model), col(CostLedger.cache_hit))
    if user_id is not None:
        query = query.where(CostLedger.user_id == user_id)

    paid: dict[tuple[str, str], tuple[int, float]] = {}
    hits: dict[tuple[str, str], int] = {}
    for stage, model, cache_hit, count, cost in session.exec(query).all():
        key = (str(stage), str(model))
        if cache_hit:
            hits[key] = hits.get(key, 0) + int(count)
        else:
            prior_count, prior_cost = paid.get(key, (0, 0.0))
            paid[key] = (prior_count + int(count), prior_cost + float(cost))

    saved = 0.0
    for key, hit_count in hits.items():
        count, cost = paid.get(key, (0, 0.0))
        if count:
            saved += (cost / count) * hit_count
    return saved


def run_detail(session: Session, version_id: str) -> dict[str, Any] | None:
    version = session.get(StoryVersion, version_id)
    if version is None:
        return None

    story = session.get(Story, version.story_id)
    user = session.get(User, story.user_id) if story else None
    jobs = list(
        session.exec(
            select(Job).where(Job.version_id == version_id).order_by(col(Job.created_at))
        ).all()
    )
    ledger = list(
        session.exec(select(CostLedger).where(CostLedger.version_id == version_id)).all()
    )

    # Cost is recorded per API call and tagged with the stage that made it, which
    # is how a stage row gets both its timing and its price in one place.
    per_stage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0,
                 "cache_hits": 0}
    )
    per_model: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"cost_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "calls": 0,
                 "cache_hits": 0}
    )
    for row in ledger:
        for bucket in (per_stage[row.stage], per_model[row.model]):
            bucket["cost_usd"] += row.cost_usd
            bucket["input_tokens"] += row.input_tokens
            bucket["output_tokens"] += row.output_tokens
            bucket["calls"] += 1
            bucket["cache_hits"] += 1 if row.cache_hit else 0

    stages = [
        {
            "stage": job.stage,
            "status": job.status,
            "attempt": job.attempt,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "duration_ms": _duration_ms(job.started_at, job.finished_at),
            "error": job.error,
            "cost_usd": round(per_stage[job.stage]["cost_usd"], 6),
            "input_tokens": per_stage[job.stage]["input_tokens"],
            "output_tokens": per_stage[job.stage]["output_tokens"],
            "calls": per_stage[job.stage]["calls"],
            "cache_hits": per_stage[job.stage]["cache_hits"],
        }
        for job in jobs
    ]

    # Stages that spend without owning a job row - the feedback interpreter is
    # the main one - would otherwise be invisible in the cost breakdown.
    job_stages = {job.stage for job in jobs}
    for stage_name, bucket in per_stage.items():
        if stage_name in job_stages:
            continue
        stages.append(
            {
                "stage": stage_name,
                "status": "succeeded",
                "attempt": 1,
                "started_at": None,
                "finished_at": None,
                "duration_ms": None,
                "error": None,
                "cost_usd": round(bucket["cost_usd"], 6),
                "input_tokens": bucket["input_tokens"],
                "output_tokens": bucket["output_tokens"],
                "calls": bucket["calls"],
                "cache_hits": bucket["cache_hits"],
            }
        )

    assets = session.exec(
        select(MediaAsset.kind, func.count(MediaAsset.id), func.coalesce(
            func.sum(MediaAsset.duration_ms), 0
        ))
        .where(MediaAsset.version_id == version_id)
        .group_by(col(MediaAsset.kind))
    ).all()

    feedback = (
        session.get(Feedback, version.created_from_feedback_id)
        if version.created_from_feedback_id
        else None
    )

    statuses = [job.status for job in jobs]
    started = min((j.started_at for j in jobs if j.started_at), default=None)
    status = _derive_status(statuses)
    finished = (
        max((j.finished_at for j in jobs if j.finished_at), default=None)
        if status not in {"running", "pending"}
        else None
    )
    regen = _regen_of(version)

    return {
        "version_id": version.id,
        "story_id": version.story_id,
        "story_title": story.title if story else None,
        "user_id": story.user_id if story else None,
        "user_email": user.email if user else None,
        "version_number": version.version_number,
        "genre": version.genre,
        "mood": version.mood,
        "parent_version_id": version.parent_version_id,
        "is_regen": regen is not None,
        "directive": regen,
        "feedback_text": feedback.raw_text if feedback else None,
        "status": status,
        "created_at": version.created_at,
        "started_at": started,
        "finished_at": finished,
        "duration_ms": _duration_ms(started, finished),
        "cost_usd": round(sum(r.cost_usd for r in ledger), 6),
        "input_tokens": sum(r.input_tokens for r in ledger),
        "output_tokens": sum(r.output_tokens for r in ledger),
        "calls": len(ledger),
        "cache_hits": sum(1 for r in ledger if r.cache_hit),
        "stages": stages,
        "by_model": [
            {"label": model, **{k: (round(v, 6) if k == "cost_usd" else v)
                                for k, v in bucket.items()}}
            for model, bucket in sorted(
                per_model.items(), key=lambda kv: kv[1]["cost_usd"], reverse=True
            )
        ],
        "assets": [
            {"kind": str(kind), "count": int(count), "duration_ms": int(duration)}
            for kind, count, duration in assets
        ],
    }
