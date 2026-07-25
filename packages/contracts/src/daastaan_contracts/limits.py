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
# Accepting less than that starves the script stage: it was picking 60 lines out
# of 6000 characters, so an uploaded story had to be cut by 80% before the stage
# that actually knows how to select scenes and dialogue ever saw it.
#
# Raising this is close to free. `raw_text` reaches four stages, two of them on
# the cheap model, so quadrupling it adds a couple of cents per story - while the
# episode length, and therefore the TTS spend that dominates the bill, stays
# fixed by MAX_LINES.
MAX_STORY_INPUT_CHARS = MAX_LINES * MAX_LINE_CHARS

MAX_FEEDBACK_CHARS = 500

# One per scene. These were 4 and 6, which is not a budget so much as a gallery
# with holes in it: the last two scenes rendered as "No artwork" and read as a
# failure rather than as a cap. Tied together so they cannot drift apart again -
# at $0.04 an image the two extra scenes cost less than a rounding error against
# the TTS for an episode.
MAX_IMAGES_PER_STORY = MAX_SCENES

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
