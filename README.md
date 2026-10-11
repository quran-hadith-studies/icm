# Earliest-recoverable-source inference for hadith families

Implementation of the Bayesian model

    P(S=s | X, C, M) ∝ P(S=s) · Z_s ,
    Z_s = Σ_G P(X|G,s) · P(C|G,s) · P(G|s,M)

where the sum runs over transmission arborescences `G` rooted at the candidate
source `s`, and ancestral matn states `A` are marginalised analytically inside
`P(X|G,s)`.

## Layout

```
hadith_phylogeny/
    data.py          Narrator / Attestation / Family containers, loaders
    collation.py     Arabic normalisation, clause & categorical variant encoding
    candidates.py    candidate parent sets P_v, candidate source set S
    text_model.py    Q_r, T_r(t)=exp(Q_r t), Felsenstein pruning, ancestral sampling
    sanad_model.py   pair-HMM (MATCH/DELETE/INSERT/SUBSTITUTE) + weighted-edit fallback
    graph_model.py   edge scores η_uv → ρ_uv, P(G|s,M), enumeration, prior sampling, MCMC move
    inference.py     exact / importance / annealed-SMC evidence, source posterior, ancestral posterior
    validation.py    simulation, recovery, permutation, prior & rate sensitivity, leave-one-collection-out
extract/             diagram (PDF mind-map) → sanad tree → Family
scripts/run_sitta.py the run on حدیث «ستة لعنهم الله»
tests/test_core.py   unit tests (pruning vs brute force, pair-HMM normalisation, ...)
outputs/             results
```

Only `numpy` is required (plus `pillow` + poppler's `pdftotext`/`pdftoppm` for the
PDF extractor).

## Method

The target is the posterior probability that narrator $s$ is the **earliest
recoverable source** of one hadith family: the earliest point that the surviving
texts and chains can be traced back to. The model does not claim to identify the
first speaker of the tradition.

### 1. Data

For $N$ surviving witnesses (a matn plus its isnād in one book):

| Symbol | Meaning |
|---|---|
| $X=(x_{ik})$ | matn matrix: witness $i=1..N$, variant unit $k=1..K$; $x_{ik}\in\{0..m_k-1\}$ or NA |
| $C_i=(c_{i1},\dots,c_{im_i})$ | recorded isnād of witness $i$, ordered source → collector |
| $a_i$ | anchor: the narrator the text is observed at (the last name in $C_i$) |
| $M$ | metadata: death year $d_v$, activity interval, cities $L_v$, documented teachers $R_v$ |
| $g(i)$ | independence group of witness $i$ (copies of one collection) |

For this family $N=44$, $K=8$: seven presence/absence clauses (الزائد في كتاب
الله، المكذّب بقدر الله، المتسلّط بالجبروت، المستحلّ لمحارم الله، التارك لسنّتي،
المستحلّ من عترتي، المتكبّر على عباد الله) and one categorical unit, the count word
(سبعة / ستة / خمسة / أربعة, $m_k=4$). A clause is NA, not absent, when the text is
abridged («بنحوه»، «وساق الحديث»، «فذكر الحديث»).

### 2. Target and decomposition

$$
P(S=s\mid X,C,M)=\frac{\pi_s Z_s}{\sum_{s'\in\mathcal S}\pi_{s'}Z_{s'}},\qquad
Z_s=\sum_{G\in\mathcal G_s}P(X\mid G,s)\,P(C\mid G,s)\,P(G\mid s,M).
$$

$Z_s$ is the marginal likelihood (evidence) of source $s$, averaged over every
transmission tree $G$ compatible with $s$. Ancestral matn states $A$ are summed
out inside $P(X\mid G,s)=\sum_A P(X,A\mid G,s)$. Posterior odds between two
sources are $\frac{\pi_{s_1}}{\pi_{s_2}}\cdot\frac{Z_{s_1}}{Z_{s_2}}$; with equal
priors this is the Bayes factor. Everything is computed in logs.

**Candidate sources** $\mathcal S$: the first narrator of every recorded chain (the
six companions), the six narrators with the most distinct recorded students
(at least two, companions excluded), and `UNKNOWN`, an unnamed source above all
of them. Default prior $\pi_s=1/|\mathcal S|$.

### 3. Chronology

Every narrator gets a year $y_v$ (lunar Hijri), from the first available source:

1. a date printed in the diagram, e.g. (287ق) or (مدینه؛ 98ق یا 106ق) → the
   interval $[98,106]$; «حدود» marks it approximate;
2. the death year of the linked biography (high or medium confidence, below);
3. linear interpolation along each chain between dated narrators (the
   companion counts as 60 when undated). Beyond the last dated narrator, add
   35 years per step; in a chain with no date other than the companion's, use
   40 years per step. The result is averaged over all chains through $v$.

The activity interval is $[y_v-45,\,y_v]$. Its end is the time key that orders
narrators.

### 4. Candidate teachers and the tree space

For each narrator $v$ the candidate teacher set $\mathcal P_v$ is:

* every recorded teacher of $v$;
* up to two recorded grand-teachers (the record dropped an intermediary), kept
  only if `compatible(u, v)` holds;
* the unnamed intermediary `UNKNOWN`.

`compatible(u, v)` is false if $u$'s activity starts after $v$'s ends, or if both
have known cities, they share none, and there is no documented teacher/meeting
link.

A tree $G\in\mathcal G_s$ is a parent assignment $pa(v)\in\mathcal P_v$ such that:

* the **active set** is $s$ plus every narrator with $y_v>y_s$ (nobody earlier than
  $s$ can descend from $s$), plus one `UNKNOWN` node whose parent is fixed to $s$;
* every active node has exactly one parent inside the active set, there are no
  cycles, and every active node is reachable from $s$;
* if a witness's anchor $a_i$ is not active, then $P(X,C\mid G,s)=0$ for every $G$,
  hence $Z_s=0$ ($\log Z_s=-\infty$).

### 5. Tree prior $P(G\mid s,M)$

Edge score (section 5 of the plan, with fixed coefficients):

$$
\eta_{uv}=3\cdot\mathbb 1[(u,v)\text{ recorded}]+1\cdot\mathbb 1[L_u\cap L_v\neq\emptyset]
+2\cdot\mathbb 1[u\in R_v]+2\cdot\mathbb 1[\text{documented meeting}],\qquad
\eta_{\texttt{UNKNOWN},v}=-4.
$$

Parent probabilities are the softmax over the candidate set, and the tree prior is
their product renormalised over the valid trees of $s$:

$$
\rho_{uv}=\frac{e^{\eta_{uv}}}{\sum_{w\in\mathcal P_v}e^{\eta_{wv}}},\qquad
P(G\mid s,M)=\frac{1}{K_s}\prod_{v\neq s}\rho_{pa(v),v},\qquad
K_s=\sum_{G'\in\mathcal G_s}\prod_{v\neq s}\rho_{pa'(v),v}.
$$

There is no meeting data in this run, so that term is always 0. $R_v$
(documented teachers) comes from the narrator database: $u\in R_v$ when
$u$ and $v$ stand next to each other, as teacher and student, in at least one of
its 577,024 isnāds.

The −4 for `UNKNOWN` only acts relative to other candidates. A narrator whose only
candidate is `UNKNOWN` (a companion under another companion's hypothesis) gets
$\rho=1$. The cost of such a hypothesis is then paid in the sanad term (section 7),
not in the prior.

### 6. Matn likelihood $P(X\mid G,s)$ — pruning

**Evolution model.** Each unit $k$ belongs to a rate class $r$. A binary unit
(clause present/absent) has generator
$Q_r=\begin{pmatrix}-\lambda^+_r&\lambda^+_r\\ \lambda^-_r&-\lambda^-_r\end{pmatrix}$.
With $\sigma=\lambda^++\lambda^-$, $\pi_0=\lambda^-/\sigma$, $\pi_1=\lambda^+/\sigma$
and $e=e^{-\sigma t}$, the transition matrix has the closed form

$$
T_r(t)=e^{Q_rt}=\begin{pmatrix}\pi_0+\pi_1e&\pi_1(1-e)\\ \pi_0(1-e)&\pi_1+\pi_0e\end{pmatrix}.
$$

A unit with $m$ states uses the equal-rate model, scaled so that one step
($t=1$) changes the state with probability $\mu_r$:

$$
P(b\mid a,t)=\begin{cases}\frac1m+\left(1-\frac1m\right)\kappa^t,&a=b\\[3pt]
\frac{1}{m-1}\left[1-P(a\mid a,t)\right],&a\neq b\end{cases},\qquad
\kappa=1-\frac{\mu_r m}{m-1}.
$$

| Rate class | $\lambda^+$ (gain) | $\lambda^-$ (loss) | $\mu$ | Used for |
|---|---:|---:|---:|---|
| omission | 0.02 | 0.12 | 0.08 | the seven clauses |
| numeric | 0.03 | 0.03 | 0.03 | the count word |
| (orthography, lexical, addition, entity, narrative) | | | | defined, unused here |

The clauses use the *omission* class: losing a clause in transmission is taken to
be six times as likely as inventing one.

**Branch lengths.** $t=1$ per narrator-to-narrator step, not calendar years. Each
surviving copy hangs off its anchor narrator by its own pendant branch, also with
$t=1$. Without that branch, two different copies transmitted by one narrator
would be a contradiction (likelihood 0).

**Pruning.** For unit $k$, let $L_{v,k}(a)$ be the probability of everything
observed below $v$, given state $a$ at $v$:

$$
L_{v,k}(a)=\prod_{i:\,a_i=v}\Big[\sum_b T_k(1)_{ab}\,\ell_{ik}(b)\Big]^{w_i}
\prod_{c\in ch(v)}\sum_b T_k(1)_{ab}\,L_{c,k}(b),
\qquad
\ell_{ik}(b)=\begin{cases}\mathbb 1[x_{ik}=b]&x_{ik}\text{ observed}\\1&x_{ik}=\text{NA}\end{cases}
$$

$$
P(X\mid G,s)=\prod_k\sum_a\pi_k(a)\,L_{s,k}(a),\qquad\pi_k(a)=1/m_k .
$$

The units are conditionally independent given $G$. Vectors are rescaled when they
underflow, and the scale is carried in the log.

**Independence weights.** $w_i=1/|\{j:g(j)=g(i)\}|$, where the group is the
collector, e.g. two witnesses from الطحاوي get $w=\tfrac12$ each. The weight is
applied as a power on the witness's likelihood factor (a power likelihood). It is
a down-weighting, not a model of copying.

### 7. Sanad likelihood $P(C\mid G,s)$ — pair-HMM

The recorded chain $C_i$ is a noisy reading of the latent path
$P_i=(s=p_1,\dots,p_n=a_i)$ from the source to the anchor in $G$:

$$
\log P(C\mid G,s)=\lambda_C\sum_i w_i\,\log P(C_i\mid P_i),
$$

with $\lambda_C=1$ by default ($\lambda_C=0$ gives the matn-only ablation).

$P(C_i\mid P_i)$ is a forward recursion over alignments with four events. With
$\tilde p_x=p_x/(p_{match}+p_{sub}+p_{del})$ and $A$ the number of confusable
identities, $F_{0,0}=0$ and

$$
F_{j,l}=\operatorname{logaddexp}\Big(
F_{j-1,l-1}+\log(1-p_{ins})+e_{jl},\;
F_{j-1,l}+\log\tilde p_{del},\;
F_{j,l-1}+\log\tfrac{p_{ins}}{A}\Big),
$$

$$
e_{jl}=\begin{cases}\log\tilde p_{match}&p_j=c_{il}\\ \log\dfrac{\tilde p_{sub}}{A-1}&\text{otherwise}\end{cases},
\qquad\log P(C_i\mid P_i)=F_{n,m_i}.
$$

The three terms are, in order: match or substitution, an intermediary omitted
from the record, and a name inserted into the record.

| Parameter | Value |
|---|---:|
| $p_{match}$ | 0.90 |
| $p_{sub}$ | 0.04 |
| $p_{del}$ (omitted intermediary) | 0.04 |
| $p_{ins}$ (inserted name) | 0.02 |
| $A$ | 2000 |

The recursion has no termination state, so it is normalised per step but not
over chain length. Strictly it is a pseudo-likelihood, as the plan anticipated
for this stage.

A weighted-edit pseudo-likelihood,
$-\lambda_d n_d-\lambda_a n_a-\lambda_s n_s$ with $\lambda=(2,3,3)$, is also
implemented (`mode="edit"`) but not used.

### 8. Estimating $Z_s$

**Exact** (small spaces): enumerate $\mathcal G_s$ and compute
$\log Z_s=\operatorname{logsumexp}_G[\log P(X|G)+\log P(C|G)+\log\prod\rho]-\log K_s$.

**Importance sampling.** Draw each parent independently from $\rho_{\cdot v}$ and
reject invalid trees. This samples exactly from $P(G\mid s,M)$, so
$w_j=P(X\mid G_j,s)P(C\mid G_j,s)$ and

$$
\log\hat Z_s=\operatorname{logsumexp}_j\log w_j-\log J,\qquad
\mathrm{ESS}=1\Big/\textstyle\sum_j\tilde w_j^2 .
$$

On this family it collapses (ESS ≈ 1 at 800 draws), so it is not used.

**Annealed SMC** (used for every reported number). The targets are

$$
p_\gamma(G)\propto P(G\mid s,M)\,\big[P(X\mid G,s)P(C\mid G,s)\big]^{\gamma},\qquad
\gamma_t=t/T,\ t=0..T .
$$

1. Draw $N_p$ particles from the prior. Particles with zero likelihood are dropped,
   and $\log\hat Z_s$ starts at $\log(\text{kept}/\text{drawn})$.
2. At each step, incremental weight $\Delta\gamma\cdot\ell(G)$, where
   $\ell=\log P(X|G)+\log P(C|G)$. Accumulate
   $\log\hat Z_s\mathrel{+}=\operatorname{logsumexp}(\log w+\Delta\gamma\ell)-\operatorname{logsumexp}(\log w)$.
3. If ESS $<0.5N_p$, resample multinomially.
4. Apply $n_{mcmc}$ Metropolis moves per particle, each targeting $p_{\gamma_t}$.
   The proposal picks a random narrator with more than one candidate and gives it
   a different candidate teacher, uniformly. Trees with a cycle or a
   disconnection are rejected. The proposal is symmetric, so the acceptance ratio
   is the target ratio.

Settings: $N_p=150$, $T=15$, $n_{mcmc}=3$ for the main runs; 80/10/2 for the rate
sensitivity runs; 60/8/2 for permutations.

### 9. Posterior summaries

All of these are computed under the best-supported source $s^\ast$, from its
weighted SMC particles $\{G_j,\tilde w_j\}$.

* **Edge posterior:** $P(pa(v)=u\mid D,s^\ast)\approx\sum_j\tilde w_j\,\mathbb 1[pa_j(v)=u]$.
* **MAP tree:** the particle maximising $\log P(G|s^\ast,M)+\ell(G)$. It is the best
  tree the sampler visited, not a guaranteed global optimum. Its **mass** is the
  total weight of particles identical to it.
* **Ancestral matn:** after pruning, one pre-order (backward) sample of $A$ per
  particle. For each child $c$ of $v$,
  $A_c\sim T(1)_{A_v,\cdot}\odot L_c/\text{norm}$; then
  $P(A_{v,k}=a\mid D)\approx\sum_j\tilde w_j\mathbb 1[A^{(j)}_{v,k}=a]$.
* **95% credible set:** the smallest set of sources, by descending posterior, with
  mass ≥ 0.95.

### 10. Analyses run

| Analysis | What changes |
|---|---|
| whole family | all 44 witnesses; $\mathcal S$ as in section 2 |
| within each line | only one companion's witnesses; $\mathcal S$ = that companion, every narrator in the line with ≥2 recorded students, `UNKNOWN` |
| matn-only ablation | $\lambda_C=0$, and $\lambda_C=0.1$ |
| source-prior sensitivity | equal; `UNKNOWN` 0.5 and the rest equal; date-informed $\pi_s\propto e^{-(y_s-y_{\min})/50}$ with `UNKNOWN` at $y_{\min}$. $Z_s$ does not depend on $\pi$, so the stored $\log Z_s$ are re-weighted |
| rate sensitivity | every class set to $\lambda^+=\lambda^-=\mu\in\{0.02,0.08,0.2\}$ |
| sanad-error sensitivity | $p_{sub}=p_{del}=\epsilon\in\{0.02,0.1,0.2\}$, $p_{match}=1-2\epsilon$ |
| permutation test | rows of $X$ shuffled among witnesses, 8 times; $p=(\#\{\text{null}\ge\text{obs}\}+1)/(8+1)$ |

### 11. Assumptions and simplifications

From the plan, as implemented:

1. One coherent hadith family per analysis.
2. Narrator identities are fixed by the diagram. A person who appears in two
   branches is two nodes; the viewer marks them as the same person when both link
   to one biography.
3. Dates and cities are treated as known point values (an interval only when the
   diagram gives two dates). They are not inferred.
4. Transmission is a rooted tree: one teacher per narrator, no conflation (a
   narrator combining two sources).
5. One unnamed intermediary `UNKNOWN`, fixed directly below the source; as a source
   hypothesis, `UNKNOWN` is a virtual root above everyone.
6. The matn is reduced to discrete variant units. The units are conditionally
   independent given the tree, and they share rates within a class.
7. Recorded isnāds are noisy observations of the true paths, with independent
   errors at each position.
8. Copies from one collection are down-weighted, not modelled as copies.

Further simplifications made in this implementation:

9. **Fixed parameters.** All rates ($\lambda,\mu$), sanad error rates and edge-score
   coefficients are set by hand and varied in the sensitivity runs; none is
   estimated from data.
10. **Restricted tree space.** Candidate teachers are only the recorded teacher, up
    to two recorded grand-teachers, and `UNKNOWN`. The graph search can repair
    omissions within a chain, but it cannot move a narrator to an unrelated branch.
11. **Coarse clock.** One transmission step is one unit of matn-change time,
    regardless of the years between teacher and student.
12. **Every later narrator must be in the tree.** Under source $s$, every narrator
    later than $s$ must attach somewhere below it, and every witness's anchor must
    be later than $s$. In the whole-family run, five of the six common links fail
    this (some witness is anchored at a narrator earlier than them), so
    $Z_s=0$; the sixth, زید بن علی بن الحسین, is admissible but very unlikely
    ($\log Z=-646.8$ against $-242.7$ for `UNKNOWN`).
13. **Uniform root prior** $\pi_k(a)=1/m_k$ for the source text; there is no
    stationary-distribution option in use.
14. **Not a proper likelihood:** the pair-HMM has no length normalisation (section 7),
    and the independence weights are a power likelihood.
15. **No fabrication model.** A chain invented wholesale up to a companion is not
    a hypothesis the model can express; errors are local (one name dropped,
    added or swapped).
16. **Automatic identity linkage.** The biography links are made by an algorithm
    (below). Only high/medium-confidence links feed the model, but some may be
    wrong.

### 12. Validation

* `python3 tests/test_core.py` checks four things:
  - pruning against brute-force summation over every ancestral state (including
    the pendant leaf branches);
  - that missing data are uninformative;
  - that $e^{Q t}$ rows sum to 1;
  - that the pair-HMM is normalised over short chains and prefers exact matches.
* `validation.source_recovery_test` simulates trees, matns and corrupted isnāds
  with a known source: 12 families, 8 witnesses, depth 3, 10 units. The true
  source was the MAP source in 12/12 cases and inside the 95% credible set in
  12/12 (`outputs/recovery_test.json`).
* These simulations come from the model itself. They show that the inference
  code recovers what the model generates, not that the model is right about
  real transmission.

### 13. Text extraction details that affect the data

* PDF text comes out in visual order. Paired punctuation is mirrored back, ( ) and
  « », and ھ is normalised to ه.
* The word الله is drawn as an unmapped ligature glyph. It is restored where its
  isolated shadda mark sits in a gap between words.
* A matn split by a highlight box, or wrapped onto a second line, is re-joined to
  the matn it continues. This recovered the only occurrence of the seventh
  clause, «المتكبّر على عباد الله» (الصدوق, 381ق).
* Clause matching uses normalised, space-free text, because glyph extraction
  inserts spaces inside words.

## The diagram pipeline (`extract/`)

The sample is a right-to-left mind-map PDF of the sanads of
حدیث «ستة لعنهم الله». The sanads are turned into a tree like this:

1. `extract_labels.py` — glyph boxes from `pdftotext -bbox-layout`, clustered
   into labels (Arabic presentation forms normalised, diacritics dropped,
   word gaps restored);
2. `raster_edges.py` — the page is rasterised and the orange connector strokes
   are recovered as connected components (the content stream does not expose
   all of them);
3. `build_graph.py` — a component's right end names the parent, each of its left
   ends names a child; label fragments not separated by a connector are one node;
4. `to_family.py` — internal nodes become narrators (dates as in Method §3),
   matn leaves become witnesses whose isnād is their root-to-collector path,
   and the matn is encoded as in Method §1.

Run: `python3 extract/build_graph.py <pdf> outputs/_work outputs/diagram_graph.json`
then `sh scripts/run_all.sh`.

## Narrator biographies (metadata M)

`external/match_narrators.py` links the diagram's narrators to the
[Hadith Knowledge Graph](https://huggingface.co/datasets/emadjumaah/hadith-kg)
(CC-BY-4.0; 49,819 narrator biographies and 577,024 isnāds). Download
`hadith-kg.db` (1.6 GB) into `external/` first.

A diagram name is matched on three signals: the share of its tokens found in a
biography's name or nickname (article-insensitive, and never on the father's name
alone), agreement of the biography's death year with ours (tight when the diagram
prints a date, loose when ours is interpolated), and chain context — whether the
candidate is attested in the KG's own isnāds as teacher or student of a candidate
for one of our recorded neighbours.

Scoring, for a diagram name $n$ and a biography $r$:

$$
\text{score}=\underbrace{c(n,r)-0.01\,e(n,r)+0.03\log_{10}(1+q_r)-0.1\,\mathbb 1[d_r\text{ unknown}]}_{\text{name}}
\;+\;\underbrace{\delta(n,r)}_{\text{dates}}\;+\;0.35\min(\text{ctx},3)
$$

* $c$ is the share of $n$'s name tokens found in $r$'s name or nickname. Tokens
  are normalised, ابن/بن are dropped, and ال is stripped for matching.
* $c$ is multiplied by 0.4 when $n$'s first name occurs in $r$ only after «بن»
  (so «أنس بن مالك» cannot match his son «نضر بن أنس بن مالك»). It is
  multiplied by 0.6 when «ابن X» in $n$ has no «بن X» in $r$.
* $e$ is the number of extra tokens in $r$; $q_r$ is $r$'s narration count.
* $\delta$, when the diagram prints a date: +0.3 if within 3 years, 0 if within 8,
  −0.6 otherwise. When our year is interpolated: +0.15 if within 30 years, 0 if
  within 70, −0.4 otherwise.
* ctx is the number of $n$'s recorded neighbours (teacher or students) that have
  a candidate standing next to $r$ in a KG isnād, in the same role.

Confidence:

* **high**: name ≥ 0.88, ctx ≥ 1, no date conflict, and a death year (or ctx ≥ 2);
* **medium**: name ≥ 0.72 with ctx ≥ 1 or a date match; or name ≥ 0.6 with ctx ≥ 2;
* **low**: anything else.

On this family, 127 of 197 narrators are linked (83 high, 44 medium). Of the
recorded edges whose two ends are both linked, 71 of 95 are attested as
teacher → student in the KG's own isnāds.

Only **high** and **medium** links enter the model:

* the biography's death year becomes a chronology anchor (dates: diagram →
  biography → linear interpolation along the chain);
* `iqama` becomes the narrator's cities (same-city edge score);
* every teacher → student pair attested in the KG's isnāds becomes teacher
  evidence (edge score +2), including for non-recorded candidate teachers.

Low-confidence links are shown in the viewer as unverified candidates only.
Collection authors with a laqab (البيهقي, ابو نعيم, …) and Shiʿi authors
(الصدوق, الكليني) are mostly absent from the KG and stay unlinked.

## Narrator database (`narrators/`)

A rule-based pipeline that replaces the per-case lookups above with data. It links
Noor's narrator ids (every slot of the 102,404 parsed isnāds in the Jāmiʿ al-Aḥādīth 4
extraction carries a resolved `RaviId`) to the classical Shia rijāl works, to WikiShia,
and to the Sunni sets, then extracts death years, Imam periods and cities with their
evidence. Everything lands in `external/narrators.sqlite` (schema: `narrators/schema.sql`).

```
python3 -m narrators noor        # Noor shards → ravi, slot, citation, adjacency      (~90 s)
python3 -m narrators fetch --book 14036          # Khoei's معجم رجال الحديث from lib.eshia.ir (resumable)
python3 -m narrators rijal       # OpenITI texts + Khoei pages → entry                 (~2 s)
python3 -m narrators wikishia    # fa/ar/en infobox crawl (resumable) + load
python3 -m narrators thaqalayn   # ThaqalaynData narrators (cross-check) + load
python3 -m narrators link        # (a) Infallibles (b) cited pages (c) alphabetical books (d) names
python3 -m narrators facts       # death years, Imam periods, cities (+ al-Ṯurayyā coordinates)
python3 -m narrators sunni       # hadith-kg / hawramani / muslimscholars / AR-Sanad
python3 -m narrators eval        # the evaluation protocol; samples → external/eval_samples.json
python3 -m narrators export --hadith-ids 106382,316143 --out meta.json --links links.json
python3 -m narrators export --dump external/narrators.json      # full dump (MCP contract)
python3 tests/test_narrators.py
```

How a narrator is linked, in order of reliability: the fourteen Infallibles by id
(`data/imam_reigns.json`); Noor's own page citations («رجال النجاشي1/16») into the
same-edition texts of al-Najāshī, Rijāl al-Ṭūsī and Ibn al-Ghaḍāʾirī, where the cited
page (±1) selects the entry and the name confirms it; for the alphabetical books in a
different edition (Khulāṣat al-aqwāl, al-Fihrist, Ibn Dāwūd) a weighted longest-increasing
subsequence over (Noor page, entry order) keeps the book's order; everything else by name
(token coverage with the father/son guard) plus tabaqa, nisba and chain-context agreement,
with the same confidence tiers as `external/match_narrators.py`. Ambiguity is kept as
several `low` rows, never resolved by guessing. Death years are read from «مات/توفي سنة …»
(number words included), «في أيام الإمام …» gives an interval, and a narrator's Imams
bound his death from below; cities come from geographic nisbas and «سكن/نزل/من أهل …»,
canonicalised through `data/city_alias.tsv` and geocoded with the al-Ṯurayyā gazetteer.
Every fact keeps its source entry and the clause it came from; conflicts are kept and
flagged at resolution time (`facts.resolve`).

`export` writes the `load_metadata` JSON the model reads (death interval, cities,
documented teachers = Noor teacher → student pairs seen in ≥ 2 isnāds) and a
`to_family`-compatible links file, so the model runs unchanged on a Noor family. Tabaqa is
deliberately not exported as `layer`: `compatible()` rejects `u.layer ≥ v.layer`, which
would forbid two companions of one Imam from transmitting to each other.

## The ICM wizard (`icm/`)

A website (ebadollah.ir/icm) that runs the whole analysis on any hadith family instead of one hand-made
diagram. One **project** per family; a user (logged in) walks through six steps and confirms each:

1. **Seed**: pick one hadith (id, `/h` link or words of the text).
2. **Search and selection**: all hadiths that share *parts* of the seed, each with its similarity per clause.
   The seed matn is cut into clauses; each clause is searched on its own (the two rarest words, and rare
   three-word phrases) in hadith-db-v02 (831k, Shia and Sunni) and the Sunni Hadith Knowledge Graph (715k),
   together with Noor's own groups/clusters, v02's consensus sets and the KG's meaning groups. Every candidate
   is scored order-free per clause (witnesses reorder clauses): `witness`, `variant`, `extended`, `abridged`,
   `partial`, `different`. The class is a suggestion; the user ticks the witnesses.
3. **Alignment**: per-clause multiple alignment of the selected witnesses and the variant units of the model
   (clause present/absent, words some witnesses omit, substituted words, number words), `icm/align.py`.
4. **Narrators**: chains are read from Noor's parsed isnāds, the KG's chains or, for everything else, the
   rule-based isnād parser (`icm/sanad_parse.py`). Every name gets candidate persons (name agreement with the
   father/son guard, chain context from Noor/KG adjacency, popularity). Names whose identity the source gives
   are *exact*; the others become **name cards** the user decides one by one, with the narrator card
   (grade, dated facts with evidence, rijāl entries, teachers/students) one click away.
5. **Phylogeny**: the recorded transmission graph as the model's `Family`, dated and placed (death
   intervals from the narrator database / KG, interpolation along chains), with chronology warnings. The user
   confirms *this exact graph* (its hash) and may enter dates.
6. **Analysis**: the earliest-source model (annealed SMC per candidate source) and the viewer.

Every decision is a new, numbered file; nothing is overwritten or deleted. Stage outputs are stored by the hash
of their inputs, so changing an identity recomputes only what depends on it, and every earlier run stays
viewable. Hadith pages can ask `/icm/api/hadith/<id>/projects` whether a project about it is running or final.

```
python3 -m icm build all             # kg.sqlite, termdf.sqlite, noor_relations.sqlite, books_meta.json  -> external/icm/
ICM_DEV_USER=me uvicorn icm.devserver:app --port 8099        # the wizard with a fake login
python3 -m icm worker                # runs the jobs (one process, one job at a time)
python3 -m icm run --seed v02:800316143 --auto               # a whole project without the UI (defaults for every decision)
python3 -m icm eval                  # recall of the search against the diagram witnesses and the consensus sets
python3 -m icm sync --server ubuntu@HOST:ebadollah/data/icm/cache    # copy the server's cache here, commit, push
python3 tests/test_icm_*.py
```

Results and decisions live in the cache repository [quran-hadith-studies/icm-cache](https://github.com/quran-hadith-studies/icm-cache)
(`projects/<id>/{project.json, decisions/, stages/, runs/}`). The server writes a plain directory; the laptop
pulls it and pushes (the organisation has deploy keys disabled, so the server holds no GitHub credential).

What to keep in mind when reading a result:

* The suggested identities are suggestions. On the «ستة لعنهم الله» family built from the corpus with every
  default accepted, the best-supported source flipped between runs of different settings (Bayes factor 1.8 to 32
  between ʿĀʾisha and an unnamed source above all narrators), because one narrator appeared as two nodes. Decide
  the cards, then read the posterior.
* SMC is stochastic; the worker fixes `PYTHONHASHSEED=0` so a run is reproducible for the same inputs.
* Search quality is measured, not claimed: of the diagram's 28 substantive witnesses present in the corpora, all
  28 are found and pre-selected, the known look-alikes are not, and 30 random v02 consensus sets are recovered
  completely (`icm eval`). Recall for other families is not measured.
* The isnād parser reads grammar only. Anonymous narrators («عدة من أصحابنا»), anaphora («عنه») and a name with
  no candidate stay name-only nodes; they are shown, never guessed.

## The HTML viewer


`outputs/sitta_viz.html` is a standalone page in Persian, right-to-left (no build
step, no network except the webfonts), built from the run's own outputs. The
whole pipeline is `sh scripts/run_all.sh`:

```
python3 scripts/export_viz.py 150   # posterior edge probabilities, MAP graph,
                                    # ancestral states, source posteriors → outputs/viz_bundle.json
python3 scripts/make_html.py        # → outputs/sitta_viz.html (+ .fragment.html, no <head>)
```

`export_viz.py` is family-agnostic: point it at another `diagram_graph.json`
and set `VIZ_TITLE` / `VIZ_HEADING` to get the same viewer for another family.

What it shows (tabs: درخت، متن‌ها، مبدأ، درخت بیشینه، حساسیت، روش و محدودیت‌ها، راهنما):

* **Tree** — the transmission tree drawn right-to-left like the source diagram:
  the hadith at the right edge, the six companion lines fanning out, the matn
  witnesses at the far left. It opens fitted to the whole tree; drag to pan,
  scroll to move, ⌘/ctrl + scroll to zoom, and the knob on a node collapses its
  subtree (the number inside a filled knob is how many nodes are hidden).
  Edge thickness and opacity track the posterior probability of that teacher →
  student link; edges in the MAP tree are drawn in the accent colour and edges
  below 0.5 are dashed. Each narrator carries its floruit and the number of
  surviving witnesses below it. Clicking a narrator shows the full posterior
  over who its teacher was and the ancestral matn probabilities at that
  narrator; clicking a matn witness shows the text, its encoded variant units,
  its isnād and its independence weight. Clicking an **edge** shows its
  posterior, its prior ρ and the parts of its score η, how many independent KG
  isnāds attest the same teacher → student pair, the witnesses that carry it,
  and the competing candidate teachers. The narrator view also shows the linked
  biography (full name, death year, city, rijāl grade, tabaqa, narration count)
  with the link's confidence. Search and the line selector narrow the drawing.
* **Texts (متن‌ها)** — all 44 witnesses' matns grouped by line, each variant
  clause highlighted in its own colour.
* **Glossary (راهنما)** — a Persian explanation of every quantity on the page.
* **Sources** — log Z, posterior and ESS for every candidate source, the
  within-line results, and the matn-only ablation.
* **Graph & MAP tree** — whether a maximum-probability tree was found, how much
  posterior mass sits on it, which edges the posterior is unsure about, and
  where the MAP tree departs from the recorded structure.
* **Sensitivity** — source priors, textual-change and sanad-error rates, the
  permutation test.
* **Method & limits** — the model in brief, the simulation validation, and what
  the result does not establish.
