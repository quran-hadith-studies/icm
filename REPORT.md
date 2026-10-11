# حدیث «ستة لعنهم الله» — earliest recoverable source

Model: `P(S=s | X, C, M) ∝ P(S=s)·Z_s`, `Z_s = Σ_G P(X|G,s)P(C|G,s)P(G|s,M)`,
ancestral matn states marginalised by pruning, `Z_s` by annealed SMC
(importance sampling from the graph prior collapses here: ESS ≈ 1 at 800 draws,
so SMC with 150 particles / 15 temperatures / 3 MCMC moves is used; reported
ESS 75–150).

## Data recovered from the diagram

| | |
|---|---|
| nodes in the mind-map | 244 (243 edges, one root, one stray text fragment) |
| narrators | 198 |
| attestations (matn witnesses) | 44 |
| variant units K | 8 |

Chronology is not diagram depth: each narrator's floruit is interpolated along
its chains between the companion's date and the collector's parsed date
(e.g. عمرة بنت عبد الرحمن → 107, against the attested 98/106).

Variant units (presence/absence unless noted; `NA` where the matn is abridged
with وساق الحديث / بنحوه):

| unit | present | absent | NA |
|---|---:|---:|---:|
| الزائد في كتاب الله | 33 | 3 | 8 |
| المكذب بقدر الله | 34 | 2 | 8 |
| المتسلط بالجبروت | 28 | 6 | 10 |
| المستحل محارم الله | 2 | 32 | 10 |
| التارك لسنتي | 25 | 9 | 10 |
| المستحل من عترتي | 34 | 0 | 10 |
| المتكبر على عباد الله | 0 | 34 | 10 |
| count word (categorical) | سبعة 9 · ستة 25 · خمسة 1 · أربعة 1 | | 8 |

## 1. Whole-family comparison

| Source | log Z | Posterior |
|---|---:|---:|
| UNKNOWN (above all companions) | −267.5 | 1.000 |
| علی بن الحسین | −375.4 | 10⁻⁴⁵ |
| عائشة بنت أبی بکر | −397.2 | 10⁻⁵⁴ |
| عمرو بن سعواء الیافعی | −536.6 | ~0 |
| سلیم بن قیس الھلالی | −541.9 | ~0 |
| أنس بن مالک | −549.8 | ~0 |
| عبد الله بن عباس | −551.5 | ~0 |

`log BF(UNKNOWN : علی بن الحسین) = 107.9`. The conclusion is unchanged under
equal, date-informed and unknown-favouring source priors, under textual-change
rates μ ∈ {0.02, 0.08, 0.2}, and under sanad-error rates {0.02, 0.10, 0.20}.

This result is structural, not textual: six companion lines are recorded, so no
*named* companion can be the root of the others' chains without re-attaching
~20 chains at enormous sanad cost. The model therefore places the source above
the companion layer. It identifies an unobserved common source; it does not
identify the Prophet, and the model cannot distinguish positions along an
unbranched unobserved stem.

## 2. Within each transmission line

| Line | witnesses | MAP source | posterior | log BF vs next |
|---|---:|---|---:|---:|
| عائشة بنت أبی بکر | 20 | عائشة | 1.000 | 48.3 (next: عبد الرحمن بن أبی الموالی) |
| علی بن الحسین | 19 | علی بن الحسین | 1.000 | 54.7 (next: زید بن علی بن الحسین) |
| عمرو بن سعواء الیافعی | 2 | عمرو بن سعواء | 0.999 | 6.6 (next: UNKNOWN) |
| سلیم بن قیس / ابن عباس / أنس | 1 each | — | — | single witness, nothing to compare |

## 3. What the matn alone supports

Re-running with the sanad term switched off (`SanadModel(weight=0)`):

| Line | matn only | sanad weighted 0.1 | full model |
|---|---|---|---|
| عائشة | **عبد الرحمن بن أبی الموالی** | عائشة | عائشة |
| علی بن الحسین | **زید بن علی بن الحسین** | علی بن الحسین | علی بن الحسین |
| عمرو بن سعواء | احمد بن رشدین المصری | عمرو بن سعواء | عمرو بن سعواء |

The textual evidence on its own reaches only the common link; it is the recorded
isnād structure that carries the source up to the companion, and even a 10%
weight on the sanad term is enough to do so. Consistently, the permutation test
(matns shuffled among sanads, 8 permutations) does not move the posterior
(p = 1.0): with this clause-level encoding the matn is nearly invariant
(المستحل من عترتي is present in 34/34 readable witnesses) and contributes almost
no discrimination between candidate sources.

## 4. Ancestral matn at the best-supported source

Posterior probability that the clause was present in the source's text
(whole-family run, at UNKNOWN):

| clause | P(present) |
|---|---:|
| الزائد في كتاب الله | 1.00 |
| المكذب بقدر الله | 1.00 |
| المتسلط بالجبروت | 1.00 |
| التارك لسنتي | 1.00 |
| المستحل من عترتي | 1.00 |
| المستحل محارم الله | 0.00 |
| المتكبر على عباد الله | 0.03 |
| count word = ستة | 0.97 (سبعة 0.03) |

Within the عائشة line the same reconstruction is less certain
(0.93–0.95 per clause, ستة 0.88), reflecting the سبعة/ستة split inside that line.

## 5. Limitations

* The posterior is dominated by the sanad term; the matn encoding (8 clause-level
  units) is too coarse to discriminate. A word-level alignment of the matns —
  the collation path in `collation.align_matns` — would give the textual term
  real power, and is the first thing to do next.
* Sanad noise is i.i.d. per narrator slot; systematic forgery (a whole chain
  fabricated to a companion) is not modelled, and it is exactly the hypothesis
  that would compete with result 1.
* Narrator identity is assumed resolved: nodes that carry the same name in
  different branches are the same person only if the diagram drew them so.
* Dates are interpolated floruits, not researched biographies; they enter the
  candidate-parent filter and the graph prior.
* `غیر واحد` ("more than one") is treated as a single node, which is wrong in
  principle — it is a collapsed set of narrators.

## Reproducing

```
python3 extract/build_graph.py "<pdf>" outputs/_work outputs/diagram_graph.json
python3 scripts/run_sitta.py 150        # whole family + sensitivity  (~4 min)
python3 scripts/run_branches.py 150     # per companion line          (~6 min)
python3 scripts/run_matn_only.py        # matn-only ablation          (~4 min)
python3 tests/test_core.py              # unit tests
```
