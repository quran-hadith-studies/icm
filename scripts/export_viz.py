"""Bundle everything the HTML viewer needs into one JSON file.

Contains: the recorded diagram tree, every narrator with its dates and posterior
parent distribution, every attestation with its matn and encoded row, posterior
edge probabilities and ancestral matn states under the best-supported source,
the MAP graph, the source posteriors (whole family and per line), and the
diagnostics and sensitivity analyses already computed.
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "extract"))
sys.path.insert(0, os.path.join(HERE, "scripts"))

import to_family
from hadith_phylogeny.candidates import UNKNOWN, build_candidate_space
from hadith_phylogeny.graph_model import GraphPrior
from hadith_phylogeny.inference import (calculate_source_posterior,
                                        posterior_summary)
from hadith_phylogeny.report import build_bundle, finite
from hadith_phylogeny.sanad_model import SanadModel
from hadith_phylogeny.text_model import VariantModel
from run_branches import branch_family

OUT = os.path.join(HERE, "outputs")


def load_json(name):
    p = os.path.join(OUT, name)
    return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None


def main(n_particles=150, seed=0):
    fam = to_family.build_family(os.path.join(OUT, "diagram_graph.json"))
    space = build_candidate_space(fam)
    students = {}
    for u, v in space.observed_edges:
        students.setdefault(u, set()).add(v)
    commons = [u for u, c in sorted(students.items(), key=lambda kv: -len(kv[1]))
               if len(c) >= 2 and u not in fam.companions][:6]
    space.sources = list(fam.companions) + commons + [UNKNOWN]
    tmodel = VariantModel(fam.variant_specs)
    phi = SanadModel()
    prior = GraphPrior(fam, space)

    t0 = time.time()
    post = calculate_source_posterior(fam, space, tmodel, phi, prior, method="smc",
                                      n_particles=n_particles, n_steps=15, n_mcmc=3,
                                      seed=seed)
    best = post.map_source()
    summ = posterior_summary(fam, post.evidences[best], tmodel, phi, prior, seed=seed)
    secs = round(time.time() - t0, 1)

    links = to_family.load_links() or {}
    bundle = build_bundle(
        fam, space, prior, post, summ, best, links=links, seconds=secs,
        title=os.environ.get("VIZ_TITLE", "Sitta la'anahum source tree"),
        heading=os.environ.get(
            "VIZ_HEADING", "حدیث «ستة لعنهم الله» — earliest recoverable source"),
        extras=dict(
            branch_results=load_json("branch_results.json"),
            matn_only=load_json("matn_only_results.json"),
            global_results=load_json("sitta_results.json"),
            recovery_test=load_json("recovery_test.json")))
    path = os.path.join(OUT, "viz_bundle.json")
    json.dump(finite(bundle), open(path, "w", encoding="utf-8"), ensure_ascii=False,
              allow_nan=False)
    print("map source", best, "| map graph mass", summ.get("map_mass"),
          "| distinct graphs", summ.get("n_distinct"), "/", summ.get("n_particles"))
    print("written", path, round(os.path.getsize(path) / 1024), "KB")


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 150)
