"""Hard caps applied before anything reaches a paid API call.

These bound both cost and blast radius. Anything user-supplied is checked against
them at the API boundary, and again in the worker, because a task can be retried
long after the request that created it.
"""

MAX_STORY_INPUT_CHARS = 6000
MAX_FEEDBACK_CHARS = 500

MAX_SCENES = 6
MAX_CHARACTERS = 6
MAX_LINES = 60

MAX_LINE_CHARS = 400
MAX_IMAGES_PER_STORY = 4

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
