"""ICM: a project wizard that runs the earliest-recoverable-source analysis on a hadith family.

Stages are pure functions of the previous stage's file plus the user's decisions.
Bump a stage's version in STAGE_VERSIONS when its output changes; a project run is
recomputed only for stages whose own version or input hash changed.
"""
PIPELINE_VERSION = "0.1"
STAGE_VERSIONS = {
    "search": 3,          # 2: hadith.ai v2 segments as the automatic parts; near-identical same-work texts folded across corpora; 3: a bare و/ف is one word with the next when texts are compared (a trailing one matches nothing), «ولا» = «لا»
    "align": 5,           # 3: the user's word-level corrections (parts.words, parts.unit_cls): user_cells, cls_user, word_unused; 4: a bare و/ف is one word with the next in slots and grids (none left at a slot's end), an added word may stand before its column; 5: every unit has a ukey that does not depend on column numbers; units the user edits (parts.units: drop, keep, states, own units over columns); private_variants / dropped_units carry full units with a reason
    "identify": 9,        # 6: hadith.ai v2 narrator tags as a chain source (before the rule-based parser); 7: their mention links and context suggestions; 8: candidates collapsed by the narrator database version (the silent kg -> noor link mapping is gone); 9: «قرأت على / سمعت من / كتب إلي» read as one transmission phrase (the preposition is no longer part of the name); user renames
    "graph": 4,           # 4: facts gathered over every id of a merged narrator; the old link-table mapping no longer runs here
    "narrators": 1,
    "infer": 3,
}
