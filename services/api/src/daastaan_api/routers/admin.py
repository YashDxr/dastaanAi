"""Admin routes.

Every route in this router depends on `require_admin`, declared once at the
router level so a new endpoint cannot accidentally ship unprotected.
"""

from datetime import UTC, datetime

from daastaan_common.models import (
    AdminSetting,
    AuditLog,
    CostLedger,
    PipelineRun,
    Story,
    User,
)
from daastaan_contracts import StoryStatus, UserRole
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import func, select

from ..deps import AdminUser, SessionDep, require_admin
from ..guards import audit, budget_cap_usd, total_spend_usd
from ..schemas import (
    AdminSettingIn,
    AdminSettingOut,
    CostRow,
    CostSummaryOut,
    RoleUpdate,
    StoryOut,
    UserOut,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/settings", response_model=list[AdminSettingOut])
def list_settings(session: SessionDep) -> list[AdminSettingOut]:
    rows = session.exec(select(AdminSetting)).all()
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
    setting = session.get(AdminSetting, key)
    if setting is None:
        setting = AdminSetting(key=key)
        session.add(setting)

    setting.value_json = body.value
    setting.updated_by = admin.id
    setting.updated_at = datetime.now(UTC)

    audit(session, actor_user_id=admin.id, action="admin.setting_update",
          target_type="admin_setting", target_id=key)
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
    return CostSummaryOut(
        total_usd=round(spent, 4),
        budget_cap_usd=cap,
        remaining_usd=round(cap - spent, 4),
        by_stage=rollup(CostLedger.stage),
        by_model=rollup(CostLedger.model),
    )


@router.get("/users", response_model=list[UserOut])
def list_users(session: SessionDep) -> list[User]:
    return list(session.exec(select(User).order_by(User.created_at)).all())


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


@router.get("/stories", response_model=list[StoryOut])
def list_all_stories(session: SessionDep) -> list[Story]:
    return list(session.exec(select(Story).order_by(Story.created_at.desc())).all())


@router.post("/stories/{story_id}/flag", response_model=StoryOut)
def flag_story(story_id: str, session: SessionDep, admin: AdminUser) -> Story:
    story = session.get(Story, story_id)
    if story is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "story not found")

    story.flagged = True
    story.status = StoryStatus.FLAGGED
    audit(session, actor_user_id=admin.id, action="admin.flag_story", target_type="story",
          target_id=story.id)
    session.commit()
    session.refresh(story)
    return story


@router.get("/runs")
def list_runs(session: SessionDep, failed_only: bool = False) -> list[PipelineRun]:
    """Doubles as the debugging tool during the build: every failed run with its
    error and a trace id to open in Langfuse or MLflow."""
    query = select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(100)
    if failed_only:
        query = query.where(PipelineRun.status == "failed")
    return list(session.exec(query).all())


@router.get("/audit")
def list_audit(session: SessionDep, limit: int = 200) -> list[AuditLog]:
    return list(
        session.exec(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(min(limit, 1000))
        ).all()
    )
