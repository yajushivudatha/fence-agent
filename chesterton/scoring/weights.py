"""Signal weights and modifiers from section 4.1 of the spec.

Every weight can be overridden in the `[weights]` table of `.chesterton.toml`.
"""

FENCE_REGISTRY = "fence_registry"
FENCE_REGISTRY_WARN = "fence_registry_warn"
REVERT_OF_REMOVAL = "revert_of_removal"
DO_NOT_REMOVE_COMMENT = "do_not_remove_comment"
INCIDENT_OR_SECURITY = "incident_or_security"
FIX_SURGICAL = "fix_surgical"
CONCURRENCY_OR_COMPAT = "concurrency_or_compat"
SUPPRESSION = "suppression"
EXPLANATORY_COMMENT = "explanatory_comment"
TESTED = "tested"
OLD_MANY_AUTHORS = "old_many_authors"

WEIGHTS = {
    FENCE_REGISTRY: 100,
    FENCE_REGISTRY_WARN: 40,
    REVERT_OF_REMOVAL: 80,
    DO_NOT_REMOVE_COMMENT: 70,
    INCIDENT_OR_SECURITY: 65,
    FIX_SURGICAL: 55,
    CONCURRENCY_OR_COMPAT: 40,
    SUPPRESSION: 35,
    # Not in the spec's table: NOTE, HACK, XXX, issue links, and URLs are too
    # common to share the 70 given to explicit "do not remove" comments.
    EXPLANATORY_COMMENT: 30,
    TESTED: 30,
    OLD_MANY_AUTHORS: 15,
}

LABELS = {
    FENCE_REGISTRY: "Declared as a fence in .fences.yml",
    FENCE_REGISTRY_WARN: "Declared as a fence in .fences.yml",
    REVERT_OF_REMOVAL: "Restored after an earlier removal",
    DO_NOT_REMOVE_COMMENT: "A comment says to keep it",
    INCIDENT_OR_SECURITY: "Added for an incident or security issue",
    FIX_SURGICAL: "Added by a small bug fix",
    CONCURRENCY_OR_COMPAT: "Added for concurrency or compatibility",
    SUPPRESSION: "Contains a deliberate lint suppression",
    EXPLANATORY_COMMENT: "A comment explains it",
    TESTED: "Referenced by tests",
    OLD_MANY_AUTHORS: "Long-lived code with several authors",
}

MODIFIER_MOVE = 0.1
MODIFIER_BULK = 0.5

SURGICAL_MAX_LINES = 10
BULK_MIN_FILES = 100
OLD_CODE_SECONDS = 2 * 365 * 24 * 3600
OLD_CODE_MIN_AUTHORS = 3
