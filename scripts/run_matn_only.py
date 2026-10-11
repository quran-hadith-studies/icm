"""How much of the source signal comes from the matn alone?

The same model is run with the sanad term switched off (SanadModel(weight=0)),
so the posterior is driven by P(X|G,s) and P(G|s,M) only.
"""
import json, os, sys
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "extract"))
import to_family
from hadith_phylogeny.candidates import UNKNOWN, build_candidate_space
from hadith_phylogeny.graph_model import GraphPrior
from hadith_phylogeny.inference import calculate_source_posterior
from hadith_phylogeny.sanad_model import SanadModel
from hadith_phylogeny.text_model import VariantModel
from run_branches import branch_family

fam = to_family.build_family(os.path.join(HERE, "outputs", "diagram_graph.json"))
names = {n: fam.narrators[n].name for n in fam.narrators}
names[UNKNOWN] = "UNKNOWN (unnamed earlier source)"
out = {}
for comp in fam.companions:
    sub = branch_family(fam, comp)
    if len(sub.attestations) < 2:
        continue
    space = build_candidate_space(sub)
    students = {}
    for u, v in space.observed_edges:
        students.setdefault(u, set()).add(v)
    commons = [u for u, c in sorted(students.items(), key=lambda kv: -len(kv[1]))
               if len(c) >= 2 and u != comp]
    space.sources = [comp] + commons + [UNKNOWN]
    prior = GraphPrior(sub, space)
    for tag, phi in (("matn_only", SanadModel(weight=0.0)),
                     ("sanad_downweighted_0.1", SanadModel(weight=0.1))):
        post = calculate_source_posterior(sub, space, VariantModel(sub.variant_specs),
                                          phi, prior, method="smc", n_particles=120,
                                          n_steps=12, n_mcmc=2, seed=0)
        out.setdefault(names[comp], {})[tag] = dict(
            map_source=names[post.map_source()],
            top={names[s]: round(post.posterior[s], 4)
                 for s in sorted(post.sources, key=lambda z: -post.posterior[z])[:4]},
            log_Z={names[s]: round(post.log_Z[s], 2) for s in post.sources})
        print(names[comp], tag, "->", names[post.map_source()])
json.dump(out, open(os.path.join(HERE, "outputs", "matn_only_results.json"), "w",
                    encoding="utf-8"), ensure_ascii=False, indent=1)
print("written")
