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
