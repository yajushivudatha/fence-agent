"""Signal weights and modifiers from section 4.1 of the spec."""

REVERT_OF_REMOVAL = "revert_of_removal"
INCIDENT_OR_SECURITY = "incident_or_security"
FIX_SURGICAL = "fix_surgical"
CONCURRENCY_OR_COMPAT = "concurrency_or_compat"
OLD_MANY_AUTHORS = "old_many_authors"

WEIGHTS = {
    REVERT_OF_REMOVAL: 80,
    INCIDENT_OR_SECURITY: 65,
    FIX_SURGICAL: 55,
    CONCURRENCY_OR_COMPAT: 40,
    OLD_MANY_AUTHORS: 15,
}

LABELS = {
    REVERT_OF_REMOVAL: "Restored after an earlier removal",
    INCIDENT_OR_SECURITY: "Added for an incident or security issue",
    FIX_SURGICAL: "Added by a small bug fix",
    CONCURRENCY_OR_COMPAT: "Added for concurrency or compatibility",
    OLD_MANY_AUTHORS: "Long-lived code with several authors",
}

MODIFIER_MOVE = 0.1
MODIFIER_BULK = 0.5

SURGICAL_MAX_LINES = 10
BULK_MIN_FILES = 100
OLD_CODE_SECONDS = 2 * 365 * 24 * 3600
OLD_CODE_MIN_AUTHORS = 3
