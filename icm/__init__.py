"""ICM: a project wizard that runs the earliest-recoverable-source analysis on a hadith family.

Stages are pure functions of the previous stage's file plus the user's decisions.
Bump a stage's version in STAGE_VERSIONS when its output changes; a project run is
recomputed only for stages whose own version or input hash changed.
"""
PIPELINE_VERSION = "0.1"
STAGE_VERSIONS = {
    "search": 2,          # 2: hadith.ai v2 segments as the automatic parts; near-identical same-work texts folded across corpora
    "align": 2,
    "identify": 7,        # 6: hadith.ai v2 narrator tags as a chain source (before the rule-based parser); 7: their mention links and context suggestions
    "graph": 3,
    "narrators": 1,
    "infer": 3,
}
