"""ICM: a project wizard that runs the earliest-recoverable-source analysis on a hadith family.

Stages are pure functions of the previous stage's file plus the user's decisions.
Bump a stage's version in STAGE_VERSIONS when its output changes; a project run is
recomputed only for stages whose own version or input hash changed.
"""
PIPELINE_VERSION = "0.1"
STAGE_VERSIONS = {
    "search": 1,
    "align": 2,
    "identify": 2,
    "graph": 1,
    "narrators": 1,
    "infer": 1,
}
