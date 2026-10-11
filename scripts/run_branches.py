"""Per-companion analysis: which narrator is the earliest recoverable source of
each transmission line of حدیث «ستة لعنهم الله»?

The whole-diagram comparison is dominated by the sanad term (no named companion
can be the root of the other companions' lines), so the informative question is
asked inside each line.
"""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "extract"))

import to_family
from hadith_phylogeny.candidates import UNKNOWN, build_candidate_space
from hadith_phylogeny.data import Family
from hadith_phylogeny.graph_model import GraphPrior
from hadith_phylogeny.inference import (ancestral_state_posterior,
                                        calculate_source_posterior)
from hadith_phylogeny.sanad_model import SanadModel
from hadith_phylogeny.text_model import VariantModel
from hadith_phylogeny.validation import permutation_test

GRAPH = os.path.join(HERE, "outputs", "diagram_graph.json")


def branch_family(fam, comp):
    atts = [a for a in fam.attestations if a.sanad and a.sanad[0] == comp]
    used = {n for a in atts for n in a.sanad}
    sub = Family(fam.name + "/" + comp, {n: fam.narrators[n] for n in used},
                 atts, fam.variant_specs)
    sub.assign_independence_weights()
    return sub


def main(n_particles=150, seed=0):
    fam = to_family.build_family(GRAPH)
    names = {n: fam.narrators[n].name for n in fam.narrators}
    names[UNKNOWN] = "UNKNOWN (unnamed earlier source)"
    results = {}
    for comp in fam.companions:
        sub = branch_family(fam, comp)
        if len(sub.attestations) < 2:
            results[names[comp]] = dict(n_attestations=len(sub.attestations),
                                        note="single attestation: nothing to compare")
            continue
        space = build_candidate_space(sub)
        students = {}
        for u, v in space.observed_edges:
            students.setdefault(u, set()).add(v)
        commons = [u for u, c in sorted(students.items(), key=lambda kv: -len(kv[1]))
                   if len(c) >= 2 and u != comp]
        space.sources = [comp] + commons + [UNKNOWN]
        tmodel = VariantModel(sub.variant_specs)
        phi = SanadModel()
        prior = GraphPrior(sub, space)
        t0 = time.time()
        post = calculate_source_posterior(sub, space, tmodel, phi, prior, method="smc",
                                          n_particles=n_particles, n_steps=15,
                                          n_mcmc=3, seed=seed)
        best = post.map_source()
        rest = sorted((s for s in post.sources if s != best),
                      key=lambda s: -post.log_Z[s])
        anc = ancestral_state_posterior(sub, post.evidences[best], tmodel, best, seed=seed)
        perm = permutation_test(sub, space, tmodel, phi, n_perm=8, method="smc",
                                n_particles=60, n_steps=8, n_mcmc=2, seed=seed)
        perm["source"] = names.get(perm["source"], perm["source"])
        results[names[comp]] = dict(
            n_attestations=len(sub.attestations), n_narrators=len(sub.narrators),
            seconds=round(time.time() - t0, 1),
            posterior={names[s]: round(post.posterior[s], 6) for s in post.sources},
            log_Z={names[s]: round(post.log_Z[s], 2) for s in post.sources},
            ess={names[s]: round(post.evidences[s].ess, 1) for s in post.sources},
            map_source=names[best],
            credible_set_95=[names[s] for s in post.credible_set(0.95)],
            log_BF_vs_next=(round(post.log_Z[best] - post.log_Z[rest[0]], 2)
                            if rest else None),
            ancestral_matn_at_map_source={sub.variant_specs[k].name:
                                          [round(float(x), 3) for x in v]
                                          for k, v in anc.items()},
            permutation_test=perm,
        )
        print(names[comp], "->", names[best], round(post.posterior[best], 3))
    path = os.path.join(HERE, "outputs", "branch_results.json")
    json.dump(results, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", path)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
