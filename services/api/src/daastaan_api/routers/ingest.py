"""Document upload.

Upload is deliberately not part of story creation. OCR on a fifty-page scan runs
for minutes, which no HTTP request should hold open, and the extracted text is
worth showing to the user before any of it is paid for. So this endpoint stores
the bytes, hands the work to a worker, and returns an id to poll. The story is
created afterwards through the ordinary text path.
"""

import structlog
from daastaan_common import get_store, ids
from daastaan_common.models import IngestJob
from daastaan_contracts import IngestStatus, limits
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..deps import CurrentUser, SessionDep
from ..dispatch import dispatch_ingest
from ..guards import audit, enforce_budget, enforce_rate_limit
from ..schemas import IngestAccepted, IngestOut

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/ingest", tags=["ingest"])

# A story is at most a few thousand characters once cleaned. Twenty megabytes is
# already a generous scanned book, and the cap is what stops an upload from
# filling the media volume.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Extensions are advisory only - the worker sniffs magic bytes and is the real
# authority. This list exists to reject the obviously wrong thing early and to
# populate the file picker.
ALLOWED_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".txt", ".md", ".markdown", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
)

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),  # docx
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"RIFF", "image/webp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def _extension(filename: str) -> str:
    _, dot, suffix = filename.rpartition(".")
    return f".{suffix.lower()}" if dot else ""


def _accepted(data: bytes) -> bool:
    """True when the bytes are a format the extractor understands.

    Checked here as well as in the worker so a rejected file costs one request
    rather than a queue round trip. Anything that decodes as UTF-8 is treated as
    plain text, which is why the magic table alone is not sufficient.
    """
    if any(data.startswith(signature) for signature, _ in _MAGIC):
        return True
    sample = data[:4096]
    if b"\x00" in sample:
        return False
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def _out(job: IngestJob) -> IngestOut:
    return IngestOut(
        id=job.id,
        filename=job.filename,
        status=job.status,
        language=job.language,
        method=job.method,
        page_count=job.page_count,
        raw_chars=job.raw_chars,
        cleaned_text=job.cleaned_text,
        title_hint=job.title_hint,
        genre_hint=job.genre_hint,
        notes=job.notes,
        error=job.error,
    )


@router.post("", response_model=IngestAccepted, status_code=status.HTTP_202_ACCEPTED)
async def upload(
    session: SessionDep,
    user: CurrentUser,
    file: UploadFile = File(...),
    language: str | None = Form(None),
) -> IngestAccepted:
    filename = (file.filename or "upload").strip()[:200]
    extension = _extension(filename)
    if extension and extension not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"{extension} files are not supported. Upload a PDF, .docx, image, or text file.",
        )

    # Read with a cap rather than trusting Content-Length, which a client sets.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "file is empty")
    if not _accepted(data):
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "That file is not a supported document. Upload a PDF, .docx, image, or text file.",
        )

    # Cleanup runs a reasoning model over the extracted text, so an upload spends
    # real credit and answers to the same cap and rate limit as a generation.
    enforce_rate_limit(session, user.id, "ingest", limits.RATE_LIMIT_GENERATIONS)
    enforce_budget(session)

    # The key is minted, never derived from the client filename: `validate_key`
    # would reject most of what a filename can contain, and the ones it would
    # accept are still attacker-chosen paths.
    object_key = f"uploads/{user.id}/{ids.new_id()}{extension or '.bin'}"
    get_store().put(object_key, data, file.content_type or "application/octet-stream")

    job = IngestJob(
        user_id=user.id,
        filename=filename,
        content_type=file.content_type or "application/octet-stream",
        size_bytes=len(data),
        object_key=object_key,
        language=language,
        status=IngestStatus.PENDING,
    )
    session.add(job)
    audit(session, actor_user_id=user.id, action="ingest.upload", target_type="ingest",
          target_id=job.id, metadata={"filename": filename, "size_bytes": len(data)})
    session.commit()
    session.refresh(job)

    dispatch_ingest(ingest_id=job.id, user_id=user.id, language=language)
    return IngestAccepted(ingest_id=job.id, filename=job.filename)


@router.get("/{ingest_id}", response_model=IngestOut)
def get_ingest(ingest_id: str, session: SessionDep, user: CurrentUser) -> IngestOut:
    job = session.get(IngestJob, ingest_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ingest not found")
    return _out(job)
