"""Run the source-posterior model on the diagram of حدیث «ستة لعنهم الله»."""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "extract"))

import to_family
from hadith_phylogeny.candidates import UNKNOWN, build_candidate_space
from hadith_phylogeny.graph_model import GraphPrior
from hadith_phylogeny.inference import (ancestral_state_posterior,
                                        calculate_source_posterior)
from hadith_phylogeny.sanad_model import SanadModel
from hadith_phylogeny.text_model import VariantModel
from hadith_phylogeny.validation import permutation_test, prior_sensitivity

GRAPH = os.path.join(HERE, "outputs", "diagram_graph.json")


def source_set(fam, space, n_common=6):
    students = {}
    for u, v in space.observed_edges:
        students.setdefault(u, set()).add(v)
    commons = [u for u, c in sorted(students.items(), key=lambda kv: -len(kv[1]))
               if len(c) >= 2 and u not in fam.companions][:n_common]
    return list(fam.companions) + commons + [UNKNOWN]


def main(n_samples=150, seed=0):
    fam = to_family.build_family(GRAPH)
    space = build_candidate_space(fam)
    space.sources = source_set(fam, space)
    tmodel = VariantModel(fam.variant_specs)
    phi = SanadModel()
    prior = GraphPrior(fam, space)
    names = {n: fam.narrators[n].name for n in fam.narrators}
    names[UNKNOWN] = "UNKNOWN (source above all companions)"

    # importance sampling from the graph prior collapses here (ESS ~ 1), so the
    # evidence is estimated by annealed SMC, as section 10 prescribes
    t0 = time.time()
    post = calculate_source_posterior(fam, space, tmodel, phi, prior, method="smc",
                                      n_particles=n_samples, n_steps=15, n_mcmc=3, seed=seed)
    secs = time.time() - t0

    out = {
        "family": fam.name,
        "n_narrators": len(fam.narrators),
        "n_attestations": len(fam.attestations),
        "variants": [s.name for s in fam.variant_specs],
        "seconds": round(secs, 1),
        "posterior": {names[s]: post.posterior[s] for s in post.sources},
        "log_Z": {names[s]: post.log_Z[s] for s in post.sources},
        "ess": {names[s]: post.evidences[s].ess for s in post.sources},
        "map_source": names[post.map_source()],
        "credible_set_95": [names[s] for s in post.credible_set(0.95)],
    }
    best = post.map_source()
    rest = [s for s in post.sources if s != best]
    if rest:
        runner = max(rest, key=lambda s: post.log_Z[s])
        out["bayes_factor_top_vs_next"] = {
            "pair": [names[best], names[runner]],
            "log_BF": post.log_Z[best] - post.log_Z[runner],
        }

    # ancestral matn at the best-supported source
    anc = ancestral_state_posterior(fam, post.evidences[best], tmodel, best, seed=seed)
    out["ancestral_matn_at_map_source"] = {
        fam.variant_specs[k].name: [round(float(x), 3) for x in v]
        for k, v in anc.items()}

    # sensitivity: source priors. The evidence Z_s does not depend on the source
    # prior, so the computed log Z are simply reweighted.
    import math
    S = list(post.sources)
    pri = {"equal": {s: 1.0 / len(S) for s in S},
           "favour_unknown": {s: (0.5 if s == UNKNOWN else 0.5 / (len(S) - 1)) for s in S}}
    # date-informed: pi_s proportional to exp(-(d_s - d_min) / 50), with the
    # unknown source placed at the earliest date
    years = {s: fam.narrators[s].activity()[1] for s in S
             if s != UNKNOWN and fam.narrators[s].activity()}
    d_min = min(years.values())
    early = {s: math.exp(-(years.get(s, d_min) - d_min) / 50.0) for s in S}
    tot = sum(early.values())
    pri["date_informed"] = {s: v / tot for s, v in early.items()}
    sens = {}
    for name_, pr_ in pri.items():
        sc = {s: math.log(pr_[s]) + post.log_Z[s] for s in S}
        mx = max(sc.values())
        z = sum(math.exp(v - mx) for v in sc.values())
        sens[name_] = {names[s]: round(math.exp(sc[s] - mx) / z, 4) for s in S}
    out["prior_sensitivity"] = sens

    # sensitivity: textual-change and sanad-error rates
    rate_rows = {}
    for mu in (0.02, 0.08, 0.2):
        from hadith_phylogeny.text_model import RateClass
        tm = VariantModel(fam.variant_specs,
                          rates={c: RateClass(mu, mu, mu) for c in
                                 ("omission", "lexical", "numeric", "addition",
                                  "entity", "narrative", "orthography")})
        p = calculate_source_posterior(fam, space, tm, phi, prior, method="smc",
                                       n_particles=80, n_steps=10, n_mcmc=2, seed=seed)
        rate_rows[f"mu={mu}"] = {names[s]: round(p.posterior[s], 4) for s in p.sources}
    for pd in (0.02, 0.10, 0.20):
        ph = SanadModel(p_match=1 - 2 * pd, p_sub=pd, p_del=pd)
        p = calculate_source_posterior(fam, space, tmodel, ph, prior, method="smc",
                                       n_particles=80, n_steps=10, n_mcmc=2, seed=seed)
        rate_rows[f"sanad_error={pd}"] = {names[s]: round(p.posterior[s], 4) for s in p.sources}
    out["rate_sensitivity"] = rate_rows

    # permutation test: matns shuffled among sanads
    out["permutation_test"] = permutation_test(fam, space, tmodel, phi, n_perm=8,
                                               method="smc", n_particles=60, n_steps=8,
                                               n_mcmc=2, seed=seed)
    out["permutation_test"]["source"] = names.get(out["permutation_test"]["source"],
                                                  out["permutation_test"]["source"])

    path = os.path.join(HERE, "outputs", "sitta_results.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps({k: out[k] for k in ("map_source", "posterior", "ess", "seconds")},
                     ensure_ascii=False, indent=1))
    print("written", path)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
