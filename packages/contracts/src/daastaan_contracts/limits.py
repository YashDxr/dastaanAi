"""Hard caps applied before anything reaches a paid API call.

These bound both cost and blast radius. Anything user-supplied is checked against
them at the API boundary, and again in the worker, because a task can be retried
long after the request that created it.
"""

MAX_SCENES = 6
MAX_CHARACTERS = 6
MAX_LINES = 60

MAX_LINE_CHARS = 400

# Sized to the longest script the pipeline can emit (MAX_LINES * MAX_LINE_CHARS).
MAX_STORY_INPUT_CHARS = MAX_LINES * MAX_LINE_CHARS

MAX_FEEDBACK_CHARS = 500

# Per-line video: one DALL-E image per dialogue line, so the cap equals MAX_LINES.
# Audio-only stories still use one image per scene as a thumbnail.
MAX_IMAGES_PER_STORY = MAX_LINES

# Shot framings cycled across per-line images for visual variety.
SHOT_TAGS: tuple[str, ...] = ("wide", "mid", "close")

# Per user, per rolling window.
RATE_LIMIT_GENERATIONS = 5
RATE_LIMIT_REGENERATIONS = 20
# A cheap, on-demand editorial pass, but still a paid model call. This lets a
# listener rerun it after revisions without turning the button into an
# unbounded prompt endpoint.
RATE_LIMIT_CONSISTENCY_CHECKS = 10
RATE_LIMIT_WINDOW_SECONDS = 3600

# Bounded concurrency for per-line TTS so one story cannot saturate the media pool.
TTS_MAX_CONCURRENCY = 6

# Local Stable Audio is deliberately bounded.  The native Mac service accepts
# only one job at a time, and keeping a generated bed short makes the mixer and
# the small MLX model predictable on 16 GB laptops.
MUSIC_MIN_DURATION_SECONDS = 5
MUSIC_MAX_DURATION_SECONDS = 60
MUSIC_DEFAULT_DURATION_SECONDS = 30
MUSIC_MAX_PROMPT_CHARS = 1000
MUSIC_MAX_ASSET_BYTES = 25 * 1024 * 1024

DEFAULT_BUDGET_CAP_USD = 100.0

# --- video editor ----------------------------------------------------------
#
# Rendering a cut costs CPU on the assembly pool rather than credit at an API, so
# these bound queue time rather than spend.

# One cut per line is already more rewriting than a 60-line script needs.
MAX_CAPTION_OVERRIDES = MAX_LINES

MAX_WATERMARK_CHARS = 40

TITLE_CARD_MIN_MS = 500
TITLE_CARD_MAX_MS = 8000

# How many saved cuts one story may hold. Each is a stored MP4, so this is the
# cap that stops an afternoon of tweaking from filling the media volume.
MAX_EDITS_PER_STORY = 12

# Renders per user, per rolling window. Sits between generations (5) and
# regenerations (20): an encode is cheap next to a pipeline run but not free.
RATE_LIMIT_RENDERS = 15

# An uploaded backing track. Generous enough for a full song at a sane bitrate,
# small enough that the request does not hold a worker slot open.
MAX_LOCAL_AUDIO_BYTES = 30 * 1024 * 1024
MAX_LOCAL_AUDIO_SECONDS = 900
