"""Single source of truth for the data-refresh lookback window.

MAX_REFRESH_DAYS caps admin refreshes (resource-exhaustion guard); the
regenerator's lookback must always cover it, or global aggregates silently
miss backfilled user rows older than the regen window (the regenerator
clamps its rebuild range to REGEN_LOOKBACK_DAYS). Keeping both here makes
that invariant structural instead of a comment in two files.
"""

# Cap on admin refresh lookback. 200 days covers codeburn's ~6-month
# client-side history so full-history pushers can be backfilled in one go.
MAX_REFRESH_DAYS = 200

# Regenerator rebuild window: must strictly exceed MAX_REFRESH_DAYS.
REGEN_LOOKBACK_DAYS = MAX_REFRESH_DAYS + 5
