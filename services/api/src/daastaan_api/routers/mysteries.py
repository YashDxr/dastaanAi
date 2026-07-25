"""Spoiler-safe mystery gameplay routes.

The complete case never leaves the server until the owner explicitly reveals it.
"""
from daastaan_common.models import Story, StoryVersion
from daastaan_contracts import StoryState, StoryStatus, TaskName
from fastapi import APIRouter, HTTPException, status

from ..deps import CurrentUser, OwnedStory, SessionDep
from ..guards import audit, enforce_budget, enforce_rate_limit
from ..dispatch import dispatch_mystery
from ..schemas import AccuseMysteryRequest, CreateMysteryRequest, DispatchAccepted, InterrogateMysteryRequest, MysteryActionOut

router = APIRouter(prefix="/mysteries", tags=["mysteries"])


def _case(state: StoryState) -> dict:
    if state.content_type != "mystery" or not state.mystery:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "mystery case not found")
    return state.mystery


def public_case(state: StoryState) -> dict:
    case = _case(state)
    play = state.mystery_play or {}
    revealed = bool(play.get("revealed"))
    discovered = set(play.get("discovered_clue_ids", []))
    clues = []
    for clue in case["clues"]:
        if revealed or clue["id"] in discovered:
            clues.append({k: v for k, v in clue.items() if k not in {"spoiler_level"}})
        else:
            clues.append({"id": clue["id"], "title": "Locked evidence", "discovery_requirement": clue["discovery_requirement"], "locked": True})
    result = {
        "id": case["id"], "title": case["title"], "premise": case["premise"], "setting": case["setting"],
        "victim": case["victim"], "initial_scene": case["initial_scene"], "difficulty": case["difficulty"],
        "suspects": [{k: v for k, v in suspect.items() if k not in {"secret", "motive", "is_culprit"}} for suspect in case["suspects"]],
        "clues": clues, "red_herrings": case["red_herrings"], "revealed": revealed,
        "interrogations": play.get("interrogations", []), "accusations": play.get("accusations", []),
    }
    if revealed:
        result["solution"] = case["solution"]
        result["culprit_id"] = case["culprit_id"]
        result["culprit_motive"] = case["culprit_motive"]
        result["crime_timeline"] = case["crime_timeline"]
        result["reveal_scene"] = case["reveal_scene"]
    return result


@router.post("", response_model=DispatchAccepted, status_code=status.HTTP_202_ACCEPTED)
def create_mystery(body: CreateMysteryRequest, session: SessionDep, user: CurrentUser) -> DispatchAccepted:
    enforce_rate_limit(session, user.id, "generate", 3)
    enforce_budget(session)
    story = Story(user_id=user.id, title=body.title, status=StoryStatus.GENERATING)
    session.add(story); session.flush()
    version = StoryVersion(story_id=story.id, version_number=1, genre=body.tone)
    session.add(version); session.flush()
    inputs = body.model_dump()
    state = StoryState(story_id=story.id, version_id=version.id, user_id=user.id, raw_text=body.premise,
                       genre_hint=body.tone, content_type="mystery", mystery={"request": inputs}, mystery_play={})
    version.state_json = state.model_dump(mode="json"); story.current_version_id = version.id
    audit(session, actor_user_id=user.id, action="mystery.create", target_type="story", target_id=story.id)
    session.commit()
    return DispatchAccepted(story_id=story.id, version_id=version.id, stages=["mystery_generation"], task_id=dispatch_mystery(story.id, version.id, user.id))


@router.get("/{story_id}", response_model=MysteryActionOut)
def get_mystery(story: OwnedStory, session: SessionDep) -> MysteryActionOut:
    version = session.get(StoryVersion, story.current_version_id)
    if not version: raise HTTPException(409, "case is not ready")
    state = StoryState.model_validate(version.state_json)
    return MysteryActionOut(status=story.status, message="case ready" if state.mystery and state.mystery.get("id") else "case generation in progress", case=public_case(state) if state.mystery and state.mystery.get("id") else {})


@router.post("/{story_id}/clues/{clue_id}/discover", response_model=MysteryActionOut)
def discover(clue_id: str, story: OwnedStory, session: SessionDep, user: CurrentUser) -> MysteryActionOut:
    version = session.get(StoryVersion, story.current_version_id); state = StoryState.model_validate(version.state_json)
    case = _case(state)
    if clue_id not in {c["id"] for c in case["clues"]}: raise HTTPException(404, "clue not found")
    play = state.mystery_play or {}; ids = set(play.get("discovered_clue_ids", [])); ids.add(clue_id); play["discovered_clue_ids"] = list(ids); state.mystery_play = play
    version.state_json = state.model_dump(mode="json"); audit(session, actor_user_id=user.id, action="mystery.clue_discover", target_type="story", target_id=story.id, metadata={"clue_id": clue_id}); session.commit()
    return MysteryActionOut(status="discovered", message="Evidence added to your case file.", case=public_case(state))


@router.post("/{story_id}/interrogate", response_model=MysteryActionOut, status_code=status.HTTP_202_ACCEPTED)
def interrogate(body: InterrogateMysteryRequest, story: OwnedStory, session: SessionDep, user: CurrentUser) -> MysteryActionOut:
    version = session.get(StoryVersion, story.current_version_id); state = StoryState.model_validate(version.state_json); case = _case(state)
    if body.suspect_id not in {s["id"] for s in case["suspects"]}: raise HTTPException(404, "suspect not found")
    from daastaan_common import celery_app
    celery_app.send_task(TaskName.INTERROGATE_MYSTERY.value, kwargs={"version_id": version.id, "user_id": user.id, "suspect_id": body.suspect_id, "question": body.question})
    return MysteryActionOut(status="queued", message="The suspect is considering your question.", case=public_case(state))


@router.post("/{story_id}/accuse", response_model=MysteryActionOut)
def accuse(body: AccuseMysteryRequest, story: OwnedStory, session: SessionDep, user: CurrentUser) -> MysteryActionOut:
    version = session.get(StoryVersion, story.current_version_id); state = StoryState.model_validate(version.state_json); case = _case(state)
    suspect = next((s for s in case["suspects"] if s["id"] == body.suspect_id), None)
    if not suspect: raise HTTPException(404, "suspect not found")
    play = state.mystery_play or {}; correct = body.suspect_id == case["culprit_id"]
    play.setdefault("accusations", []).append({"suspect_id": body.suspect_id, "correct": correct})
    state.mystery_play = play; version.state_json = state.model_dump(mode="json"); audit(session, actor_user_id=user.id, action="mystery.accuse", target_type="story", target_id=story.id); session.commit()
    message = "Your case is compelling. You may reveal the solution." if correct else "The evidence does not support that accusation. Keep investigating."
    return MysteryActionOut(status="correct" if correct else "incorrect", message=message, case=public_case(state))


@router.post("/{story_id}/reveal", response_model=MysteryActionOut)
def reveal(story: OwnedStory, session: SessionDep, user: CurrentUser) -> MysteryActionOut:
    version = session.get(StoryVersion, story.current_version_id); state = StoryState.model_validate(version.state_json); _case(state)
    play = state.mystery_play or {}; play["revealed"] = True; state.mystery_play = play; version.state_json = state.model_dump(mode="json")
    audit(session, actor_user_id=user.id, action="mystery.reveal", target_type="story", target_id=story.id); session.commit()
    return MysteryActionOut(status="revealed", message="The full truth is now in your case file.", case=public_case(state))
