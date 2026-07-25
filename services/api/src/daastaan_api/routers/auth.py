from daastaan_common import get_settings
from daastaan_common.models import User
from daastaan_contracts import UserRole
from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import select

from ..deps import CurrentUser, SessionDep
from ..guards import audit
from ..schemas import LoginRequest, SignupRequest, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.jwt_ttl_seconds,
        path="/",
    )


@router.post("/signup", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, response: Response, session: SessionDep) -> User:
    existing = session.exec(select(User).where(User.email == body.email)).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "email already registered")

    user = User(email=body.email, password_hash=hash_password(body.password), role=UserRole.USER)
    session.add(user)
    audit(session, actor_user_id=None, action="auth.signup", target_type="user", target_id=user.id)
    session.commit()
    session.refresh(user)

    _set_session_cookie(response, create_access_token(user.id, user.role))
    return user


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, response: Response, session: SessionDep) -> User:
    user = session.exec(select(User).where(User.email == body.email)).first()

    # Failed logins are audited too, so misuse is detectable rather than silent.
    if user is None or not verify_password(body.password, user.password_hash):
        audit(
            session,
            actor_user_id=user.id if user else None,
            action="auth.login_failed",
            metadata={"email": body.email},
        )
        session.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "account disabled")

    audit(session, actor_user_id=user.id, action="auth.login")
    session.commit()
    _set_session_cookie(response, create_access_token(user.id, user.role))
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user
