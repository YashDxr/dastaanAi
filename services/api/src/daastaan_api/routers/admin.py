"""Admin routes.

Every route in this router depends on `require_admin`, declared once at the
router level so a new endpoint cannot accidentally ship unprotected.
"""

import math
import re
from collections import defaultdict
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from daastaan_common.models import (
    AdminSetting,
    AuditLog,
    CostLedger,
    MediaAsset,
    Story,
    StoryVersion,
    User,
)
from daastaan_contracts import (
    AssetKind,
    CharacterRole,
    ReviewAction,
    ReviewStatus,
    StageName,
    StoryState,
    StoryStatus,
    UserRole,
    limits,
)
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlmodel import col, func, select

from .. import analytics
from ..deps import AdminUser, SessionDep, require_admin
from ..guards import audit, budget_cap_usd, total_spend_usd
from ..schemas import (
    AdminSettingIn,
    AdminSettingOut,
    AdminStoryDetailOut,
    AdminStoryOwnerOut,
    AdminStorySummaryOut,
    AssetCoverageOut,
    AssetOut,
    AuditLogOut,
    CostRow,
    CostSummaryOut,
    RoleUpdate,
    RunDetailOut,
    RunSummaryOut,
    StoryQualityOut,
    StoryReviewOut,
    StoryReviewRequest,
    UserCostDetailOut,
    UserOut,
    UserSummaryOut,
    VersionOut,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


# Settings are deliberately a small allow-list. Workers only consume the first
# three today; feature flags are retained as a validated, audited control plane
# rather than letting an operator persist arbitrary JSON that nothing understands.
_SETTING_KEYS = frozenset({"budget_cap_usd", "models", "voice_presets", "feature_flags"})
_MODEL_OVERRIDE_KEYS = frozenset(
    {"reasoning", "light", "tts", "image", *(stage.value for stage in StageName)}
)
_VOICE_ROLE_KEYS = frozenset(role.value for role in CharacterRole)
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_MAX_BUDGET_CAP_USD = 1_000_000.0


def _invalid_setting(detail: str) -> None:
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail)


def _valid_identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(_IDENTIFIER_RE.fullmatch(value))


def _validate_runtime_setting(key: str, value: dict[str, Any]) -> None:
    """Reject malformed runtime controls before they can affect a worker.

    This is intentionally validation, not a capability system: endpoints remain
    admin-only. The goal is to prevent a typo from silently becoming a durable
    no-op or an unusable model/voice selector during a live run.
    """
    if key not in _SETTING_KEYS:
        _invalid_setting(f"unsupported setting {key!r}")

    if key == "budget_cap_usd":
        if set(value) != {"amount"}:
            _invalid_setting("budget_cap_usd must contain exactly an 'amount' field")
        amount = value["amount"]
        if (
            isinstance(amount, bool)
            or not isinstance(amount, int | float)
            or not math.isfinite(float(amount))
            or not 0 <= float(amount) <= _MAX_BUDGET_CAP_USD
        ):
            _invalid_setting(
                "budget_cap_usd.amount must be a finite number between 0 and "
                f"{_MAX_BUDGET_CAP_USD:g}"
            )
        return

    if key == "models":
        unknown = set(value) - _MODEL_OVERRIDE_KEYS
        if unknown:
            _invalid_setting(f"unknown model override key(s): {', '.join(sorted(unknown))}")
        invalid = [name for name, model in value.items() if not _valid_identifier(model)]
        if invalid:
            _invalid_setting(
                "model override values must be safe model identifiers: "
                f"{', '.join(invalid)}"
            )
        return

    if key == "voice_presets":
        unknown = set(value) - _VOICE_ROLE_KEYS
        if unknown:
            _invalid_setting(f"unknown voice role(s): {', '.join(sorted(unknown))}")
        invalid = [role for role, voice in value.items() if not _valid_identifier(voice)]
        if invalid:
            _invalid_setting(f"voice preset values must be safe identifiers: {', '.join(invalid)}")
        return

    # Feature flags are future-facing, but their shape is fixed now so a value
    # cannot accidentally turn into executable or model-routing configuration.
    invalid = [
        name
        for name, enabled in value.items()
        if not _valid_identifier(name) or not isinstance(enabled, bool)
    ]
    if invalid:
        _invalid_setting("feature_flags must map safe flag names to booleans")


@router.get("/settings", response_model=list[AdminSettingOut])
def list_settings(session: SessionDep) -> list[AdminSettingOut]:
    rows = session.exec(select(AdminSetting).order_by(AdminSetting.key)).all()
    return [
        AdminSettingOut(
            key=row.key, value=row.value_json, updated_by=row.updated_by, updated_at=row.updated_at
        )
        for row in rows
    ]


@router.put("/settings/{key}", response_model=AdminSettingOut)
def upsert_setting(
    key: str, body: AdminSettingIn, session: SessionDep, admin: AdminUser
) -> AdminSettingOut:
    """Runtime knobs: per-stage model overrides, voice presets, feature flags,
    budget cap. Workers read these at task time, so changes take effect on the
    next task without a redeploy."""
    _validate_runtime_setting(key, body.value)
    setting = session.get(AdminSetting, key)
    previous_value: dict[str, Any] | None = None
    if setting is None:
        setting = AdminSetting(key=key)
        session.add(setting)
    else:
        # Audit a snapshot rather than the mutable JSON object SQLAlchemy is
        # tracking, otherwise a later in-place edit would rewrite history.
        previous_value = deepcopy(setting.value_json)

    setting.value_json = deepcopy(body.value)
    setting.updated_by = admin.id
    setting.updated_at = datetime.now(UTC)

    audit(
        session,
        actor_user_id=admin.id,
        action="admin.setting_update",
        target_type="admin_setting",
        target_id=key,
        metadata={
            "operation": "create" if previous_value is None else "update",
            "previous_value": previous_value,
            "new_value": deepcopy(body.value),
            "reason": body.reason.strip() if body.reason else None,
        },
    )
    session.commit()
    session.refresh(setting)
    return AdminSettingOut(
        key=setting.key,
        value=setting.value_json,
        updated_by=setting.updated_by,
        updated_at=setting.updated_at,
    )


@router.get("/costs", response_model=CostSummaryOut)
def cost_summary(session: SessionDep) -> CostSummaryOut:
    def rollup(column) -> list[CostRow]:  # type: ignore[no-untyped-def]
        rows = session.exec(
            select(
                column,
                func.count(CostLedger.id),
                func.coalesce(func.sum(CostLedger.input_tokens), 0),
                func.coalesce(func.sum(CostLedger.output_tokens), 0),
                func.coalesce(func.sum(CostLedger.cost_usd), 0.0),
            ).group_by(column)
        ).all()
        return [
            CostRow(
                label=str(label),
                calls=int(calls),
                input_tokens=int(tin),
                output_tokens=int(tout),
                cost_usd=round(float(cost), 4),
            )
            for label, calls, tin, tout, cost in rows
        ]

    spent = total_spend_usd(session)
    cap = budget_cap_usd(session)
    hits = int(
        session.exec(
            select(func.count()).select_from(CostLedger).where(col(CostLedger.cache_hit))
        ).one()
    )
    return CostSummaryOut(
        total_usd=round(spent, 4),
        budget_cap_usd=cap,
        remaining_usd=round(cap - spent, 4),
        cache_savings_usd=round(analytics.estimate_cache_savings(session), 4),
        cache_hits=hits,
        by_stage=rollup(CostLedger.stage),
        by_model=rollup(CostLedger.model),
    )


@router.get("/users", response_model=list[UserSummaryOut])
def list_users(session: SessionDep) -> list[UserSummaryOut]:
    """Accounts with what each has spent, so a runaway user is visible from the
    list rather than only after drilling in."""
    return [UserSummaryOut.model_validate(row) for row in analytics.user_summaries(session)]


@router.get("/users/{user_id}/costs", response_model=UserCostDetailOut)
def user_costs(user_id: str, session: SessionDep) -> UserCostDetailOut:
    detail = analytics.user_cost_detail(session, user_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    return UserCostDetailOut.model_validate(detail)


@router.put("/users/{user_id}/role", response_model=UserOut)
def set_role(user_id: str, body: RoleUpdate, session: SessionDep, admin: AdminUser) -> User:
    if body.role not in set(UserRole):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "unknown role")

    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if target.id == admin.id and body.role != UserRole.ADMIN:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot demote yourself")

    previous, target.role = target.role, body.role
    audit(session, actor_user_id=admin.id, action="admin.role_change", target_type="user",
          target_id=target.id, metadata={"from": previous, "to": body.role})
    session.commit()
    session.refresh(target)
    return target


@router.get("/stories", response_model=list[AdminStorySummaryOut])
def list_all_stories(
    session: SessionDep,
    review_status: ReviewStatus | None = None,
    flagged: bool | None = None,
    story_status: StoryStatus | None = None,
    limit: int = Query(default=100, ge=1, le=200),
) -> list[AdminStorySummaryOut]:
    """The operator review queue, newest first.

    Asset coverage is calculated against the *current* immutable version rather
    than the story's global asset history, so a previous branch can never make a
    newly-regenerated episode look ready by accident.
    """
    query = select(Story)
    if review_status is not None:
        query = query.where(Story.review_status == review_status.value)
    if flagged is not None:
        query = query.where(Story.flagged == flagged)
    if story_status is not None:
        query = query.where(Story.status == story_status.value)
    stories = list(
        session.exec(query.order_by(col(Story.created_at).desc()).limit(limit)).all()
    )
    return _story_summaries(session, stories)


@router.get("/stories/{story_id}/quality", response_model=StoryQualityOut)
def get_story_quality(story_id: str, session: SessionDep) -> StoryQualityOut:
    story = _admin_story_or_404(session, story_id)
    version, assets = _current_version_assets(session, story)
    return _quality_report(version, assets)


@router.get("/stories/{story_id}", response_model=AdminStoryDetailOut)
def get_admin_story(story_id: str, session: SessionDep) -> AdminStoryDetailOut:
    story = _admin_story_or_404(session, story_id)
    version, assets = _current_version_assets(session, story)
    owner = session.get(User, story.user_id)
    history = list(
        session.exec(
            select(AuditLog)
            .where(
                AuditLog.target_type == "story",
                AuditLog.target_id == story.id,
                col(AuditLog.action).in_(["admin.story_review", "admin.flag_story"]),
            )
            .order_by(col(AuditLog.created_at).desc())
        ).all()
    )
    summary = _story_summary(story, version, assets, owner)
    return AdminStoryDetailOut(
        **summary.model_dump(),
        version=VersionOut.model_validate(version, from_attributes=True) if version else None,
        state=deepcopy(version.state_json) if version else None,
        assets=[_asset_out(asset) for asset in assets],
        review_history=[_audit_out(entry) for entry in history],
    )


@router.post("/stories/{story_id}/review", response_model=AdminStorySummaryOut)
def review_story(
    story_id: str,
    body: StoryReviewRequest,
    session: SessionDep,
    admin: AdminUser,
) -> AdminStorySummaryOut:
    story = _admin_story_or_404(session, story_id)
    version, assets = _current_version_assets(session, story)
    quality = _quality_report(version, assets)
    _apply_story_review(session, story, body.action, body.note, admin.id, quality)
    session.commit()
    session.refresh(story)
    return _story_summary(story, version, assets, session.get(User, story.user_id))


@router.post("/stories/{story_id}/flag", response_model=AdminStorySummaryOut)
def flag_story(story_id: str, session: SessionDep, admin: AdminUser) -> AdminStorySummaryOut:
    """Compatibility route for the original one-click moderation control.

    New clients should use ``/review`` because it requires a reviewer note for
    flags. This endpoint remains reversible and produces the same rich audit
    event, but it marks the action as legacy so operators can distinguish it.
    """
    story = _admin_story_or_404(session, story_id)
    version, assets = _current_version_assets(session, story)
    quality = _quality_report(version, assets)
    _apply_story_review(
        session,
        story,
        ReviewAction.FLAG,
        None,
        admin.id,
        quality,
        legacy_endpoint=True,
    )
    session.commit()
    session.refresh(story)
    return _story_summary(story, version, assets, session.get(User, story.user_id))


@router.get("/runs", response_model=list[RunSummaryOut])
def list_runs(
    session: SessionDep, failed_only: bool = False, limit: int = analytics.MAX_RUNS
) -> list[RunSummaryOut]:
    """Every pipeline execution, newest first.

    A run is a `StoryVersion` rolled up from the `jobs` and `cost_ledger` rows it
    produced. The `pipeline_runs` table this used to read is never written to by
    anything, which is why the tab was always empty.
    """
    return [
        RunSummaryOut.model_validate(row)
        for row in analytics.run_summaries(session, limit=limit, failed_only=failed_only)
    ]


@router.get("/runs/{version_id}", response_model=RunDetailOut)
def get_run(version_id: str, session: SessionDep) -> RunDetailOut:
    detail = analytics.run_detail(session, version_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return RunDetailOut.model_validate(detail)


@router.get("/audit", response_model=list[AuditLogOut])
def list_audit(session: SessionDep, limit: int = 200) -> list[AuditLogOut]:
    rows = session.exec(
        select(AuditLog).order_by(col(AuditLog.created_at).desc()).limit(min(limit, 1000))
    ).all()
    return [_audit_out(row) for row in rows]


# --- story review queue ----------------------------------------------------


def _admin_story_or_404(session: SessionDep, story_id: str) -> Story:
    story = session.get(Story, story_id)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "story not found")
    return story


def _current_version_assets(
    session: SessionDep, story: Story
) -> tuple[StoryVersion | None, list[MediaAsset]]:
    version = (
        session.get(StoryVersion, story.current_version_id) if story.current_version_id else None
    )
    if version is None:
        return None, []
    assets = list(
        session.exec(
            select(MediaAsset)
            .where(MediaAsset.version_id == version.id)
            .order_by(col(MediaAsset.created_at))
        ).all()
    )
    return version, assets


def _story_summaries(session: SessionDep, stories: list[Story]) -> list[AdminStorySummaryOut]:
    """Build a page in a fixed number of queries rather than one per row."""
    if not stories:
        return []

    version_ids = [story.current_version_id for story in stories if story.current_version_id]
    versions = (
        session.exec(select(StoryVersion).where(col(StoryVersion.id).in_(version_ids))).all()
        if version_ids
        else []
    )
    versions_by_id = {version.id: version for version in versions}

    assets_by_version: dict[str, list[MediaAsset]] = defaultdict(list)
    if version_ids:
        for asset in session.exec(
            select(MediaAsset)
            .where(col(MediaAsset.version_id).in_(version_ids))
            .order_by(col(MediaAsset.created_at))
        ).all():
            assets_by_version[asset.version_id].append(asset)

    owner_ids = list({story.user_id for story in stories})
    owners = {
        user.id: user
        for user in session.exec(select(User).where(col(User.id).in_(owner_ids))).all()
    }
    return [
        _story_summary(
            story,
            versions_by_id.get(story.current_version_id or ""),
            assets_by_version.get(story.current_version_id or "", []),
            owners.get(story.user_id),
        )
        for story in stories
    ]


def _story_summary(
    story: Story,
    version: StoryVersion | None,
    assets: list[MediaAsset],
    owner: User | None,
) -> AdminStorySummaryOut:
    return AdminStorySummaryOut(
        id=story.id,
        title=story.title,
        status=story.status,
        current_version_id=story.current_version_id,
        flagged=story.flagged,
        created_at=story.created_at,
        owner=AdminStoryOwnerOut(id=owner.id, email=owner.email) if owner else None,
        review=_review_out(story),
        quality=_quality_report(version, assets),
    )


def _review_status(value: str | None) -> ReviewStatus:
    """Legacy rows were created before review metadata; treat them as pending."""
    try:
        return ReviewStatus(value or ReviewStatus.PENDING)
    except ValueError:
        return ReviewStatus.PENDING


def _review_out(story: Story) -> StoryReviewOut:
    return StoryReviewOut(
        status=_review_status(story.review_status),
        note=story.review_note,
        reviewed_by=story.reviewed_by,
        reviewed_at=story.reviewed_at,
    )


def _asset_out(asset: MediaAsset) -> AssetOut:
    return AssetOut(
        id=asset.id,
        kind=asset.kind,
        line_id=asset.line_id,
        scene_id=asset.scene_id,
        content_type=asset.content_type,
        duration_ms=asset.duration_ms,
        url=f"/api/media/{asset.id}",
    )


def _audit_out(entry: AuditLog) -> AuditLogOut:
    return AuditLogOut(
        id=entry.id,
        actor_user_id=entry.actor_user_id,
        action=entry.action,
        target_type=entry.target_type,
        target_id=entry.target_id,
        metadata=deepcopy(entry.metadata_json),
        created_at=entry.created_at,
    )


def _single_asset_coverage(
    kind: AssetKind, assets: list[MediaAsset], *, expected: int, required: bool
) -> AssetCoverageOut:
    matching = [asset for asset in assets if asset.kind == kind.value]
    complete = min(sum(bool(asset.object_key) for asset in matching), expected)
    placeholders = sum(not asset.object_key for asset in matching)
    return AssetCoverageOut(
        kind=kind.value,
        expected=expected,
        complete=complete,
        missing=max(expected - complete, 0),
        placeholders=placeholders,
        required=required,
    )


def _target_coverage(
    kind: AssetKind,
    assets: list[MediaAsset],
    *,
    target_ids: list[str],
    target_field: str,
    required: bool,
) -> AssetCoverageOut:
    targets = set(target_ids)
    matching = [asset for asset in assets if asset.kind == kind.value]
    complete_ids = {
        getattr(asset, target_field)
        for asset in matching
        if asset.object_key and getattr(asset, target_field) in targets
    }
    placeholder_ids = {
        getattr(asset, target_field)
        for asset in matching
        if not asset.object_key and getattr(asset, target_field) in targets
    }
    complete = len(complete_ids)
    return AssetCoverageOut(
        kind=kind.value,
        expected=len(targets),
        complete=complete,
        missing=max(len(targets) - complete, 0),
        placeholders=len(placeholder_ids),
        required=required,
    )


def _video_image_coverage(state: StoryState, assets: list[MediaAsset]) -> AssetCoverageOut:
    """Video accepts a scene-level image as a fallback for a line-level frame.

    Counting only line assets would produce a false failure for valid older
    versions that predate the per-line image strategy, even though the video
    composer explicitly supports that fallback.
    """
    target_lines: list[str] = []
    for scene in state.scenes:
        scene_lines = sorted(
            (line for line in state.lines if line.scene_id == scene.id), key=lambda line: line.index
        )
        for line in scene_lines:
            if len(target_lines) >= limits.MAX_IMAGES_PER_STORY:
                break
            target_lines.append(line.id)
        if len(target_lines) >= limits.MAX_IMAGES_PER_STORY:
            break

    target_set = set(target_lines)
    lines_by_scene: dict[str, set[str]] = defaultdict(set)
    for line in state.lines:
        if line.id in target_set:
            lines_by_scene[line.scene_id].add(line.id)

    complete_ids: set[str] = set()
    placeholder_ids: set[str] = set()
    for asset in assets:
        if asset.kind != AssetKind.SCENE_IMAGE.value:
            continue
        covered = (
            {asset.line_id}
            if asset.line_id in target_set
            else lines_by_scene.get(asset.scene_id or "", set())
        )
        if asset.object_key:
            complete_ids.update(covered)
        else:
            placeholder_ids.update(covered)

    return AssetCoverageOut(
        kind=AssetKind.SCENE_IMAGE.value,
        expected=len(target_set),
        complete=len(complete_ids),
        missing=max(len(target_set) - len(complete_ids), 0),
        placeholders=len(placeholder_ids),
        required=True,
    )


def _quality_report(version: StoryVersion | None, assets: list[MediaAsset]) -> StoryQualityOut:
    if version is None:
        return StoryQualityOut(
            version_id=None,
            state_available=False,
            ready_for_review=False,
            required_missing=1,
            placeholder_count=0,
            coverage=[],
            warnings=["No current version is available for review."],
        )

    try:
        state = StoryState.model_validate(version.state_json)
    except (TypeError, ValidationError):
        final_episode = _single_asset_coverage(
            AssetKind.FINAL_EPISODE, assets, expected=1, required=True
        )
        return StoryQualityOut(
            version_id=version.id,
            state_available=False,
            ready_for_review=False,
            required_missing=final_episode.missing,
            placeholder_count=final_episode.placeholders,
            coverage=[final_episode],
            warnings=["Current version state could not be validated; coverage cannot be verified."],
        )

    output_format = (
        state.output_format if state.output_format in {"audio", "video", "both"} else "audio"
    )
    line_audio = _target_coverage(
        AssetKind.LINE_AUDIO,
        assets,
        target_ids=[line.id for line in state.lines],
        target_field="line_id",
        required=True,
    )
    if output_format in {"video", "both"}:
        scene_images = _video_image_coverage(state, assets)
    else:
        # Audio stories can use image art in the scene timeline, but it is not
        # a release blocker: the playable final episode is the required asset.
        scene_images = _target_coverage(
            AssetKind.SCENE_IMAGE,
            assets,
            target_ids=[scene.id for scene in state.scenes[: limits.MAX_IMAGES_PER_STORY]],
            target_field="scene_id",
            required=False,
        )
    final_episode = _single_asset_coverage(
        AssetKind.FINAL_EPISODE, assets, expected=1, required=True
    )
    final_video = _single_asset_coverage(
        AssetKind.FINAL_VIDEO,
        assets,
        expected=1 if output_format in {"video", "both"} else 0,
        required=output_format in {"video", "both"},
    )
    # BGM is intentionally optional: a local score service being unavailable
    # must not prevent a fully assembled narration episode from being reviewed.
    music_bed = _single_asset_coverage(AssetKind.MUSIC_BED, assets, expected=1, required=False)
    coverage = [line_audio, scene_images, final_episode, final_video, music_bed]
    required_missing = sum(item.missing for item in coverage if item.required)
    required_placeholders = sum(item.placeholders for item in coverage if item.required)
    warnings: list[str] = []
    if required_missing:
        warnings.append(f"{required_missing} required asset(s) are missing.")
    if required_placeholders:
        warnings.append(f"{required_placeholders} required asset(s) are still in progress.")

    return StoryQualityOut(
        version_id=version.id,
        state_available=True,
        ready_for_review=required_missing == 0 and required_placeholders == 0,
        required_missing=required_missing,
        placeholder_count=sum(item.placeholders for item in coverage),
        coverage=coverage,
        warnings=warnings,
    )


def _restored_story_status(story: Story, quality: StoryQualityOut) -> StoryStatus:
    try:
        previous = StoryStatus(story.status_before_flag or "")
    except ValueError:
        previous = None
    if previous and previous != StoryStatus.FLAGGED:
        return previous
    # Older rows were flagged before the reversible fields existed. Never claim
    # readiness without a complete, verifiable current version.
    return StoryStatus.READY if quality.ready_for_review else StoryStatus.DRAFT


def _review_snapshot(story: Story) -> dict[str, Any]:
    return {
        "review_status": _review_status(story.review_status).value,
        "review_note": story.review_note,
        "flagged": story.flagged,
        "story_status": story.status,
    }


def _clear_flag_state(story: Story, quality: StoryQualityOut) -> tuple[ReviewStatus, str | None]:
    restored_status = _review_status(story.review_status_before_flag)
    restored_note = story.review_note_before_flag
    story.flagged = False
    story.status = _restored_story_status(story, quality)
    story.review_status_before_flag = None
    story.review_note_before_flag = None
    story.status_before_flag = None
    return restored_status, restored_note


def _apply_story_review(
    session: SessionDep,
    story: Story,
    action: ReviewAction,
    note: str | None,
    actor_id: str,
    quality: StoryQualityOut,
    *,
    legacy_endpoint: bool = False,
) -> None:
    """Apply a review transition and write one self-describing audit event."""
    if story.status == StoryStatus.GENERATING:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a story cannot be reviewed while its current version is generating",
        )
    if action == ReviewAction.CLEAR_FLAG and not story.flagged:
        raise HTTPException(status.HTTP_409_CONFLICT, "story is not flagged")
    if action == ReviewAction.APPROVE:
        effective_status = _restored_story_status(story, quality) if story.flagged else story.status
        if effective_status != StoryStatus.READY or not quality.ready_for_review:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "only a ready story with complete required assets can be approved",
            )

    before = _review_snapshot(story)
    if action == ReviewAction.FLAG:
        if not story.flagged:
            story.review_status_before_flag = _review_status(story.review_status).value
            story.review_note_before_flag = story.review_note
            story.status_before_flag = story.status
        story.flagged = True
        story.status = StoryStatus.FLAGGED
        story.review_status = ReviewStatus.FLAGGED
        story.review_note = note
    elif action == ReviewAction.CLEAR_FLAG:
        restored_status, restored_note = _clear_flag_state(story, quality)
        story.review_status = restored_status
        story.review_note = restored_note
    else:
        if story.flagged:
            _clear_flag_state(story, quality)
        story.review_status = (
            ReviewStatus.APPROVED
            if action == ReviewAction.APPROVE
            else ReviewStatus.CHANGES_REQUESTED
        )
        story.review_note = note

    story.reviewed_by = actor_id
    story.reviewed_at = datetime.now(UTC)
    session.add(story)
    audit(
        session,
        actor_user_id=actor_id,
        action="admin.story_review",
        target_type="story",
        target_id=story.id,
        metadata={
            "action": action.value,
            "note": note,
            "legacy_endpoint": legacy_endpoint,
            "before": before,
            "after": _review_snapshot(story),
        },
    )
