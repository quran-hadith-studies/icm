"""Semantic search: the index of text parts (icm/build/parts.py, icm/semantic.py) over a tiny corpus with a toy encoder, the
numpy encoder against torch when both are there, and how the search takes hadiths found by meaning into its candidates."""
import json
import os
import sqlite3
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, corpus, search, semantic
from icm.build import parts, vec

TEXTS = {                                      # part id -> text
    0: "اقیموا الصلاه واتوا الزکاه",
    1: "یقیم الصلاه ویوتی الزکاه",
    2: "ان الله لا یغفر ان یشرک به",
    3: "من کنت مولاه فهذا علی مولاه",
    4: "یغفر الله ما دون ذلک لمن یشاء",
}
OCC = [(0, 1), (0, 6), (1, 2), (2, 3), (3, 4), (4, -5)]          # (part id, hadith key; negative: the KG): hadith 6 has the text of 1 too
DIM = 96


class ToyEncoder:
    """Hashed character 3-grams: texts that share letters are close."""
    cfg = dict(hidden=DIM)

    def encode(self, texts):
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for j, t in enumerate(texts):
            t = t.replace("query: ", "").replace(" ", "_")
            for i in range(len(t) - 2):
                out[j, hash(t[i:i + 3]) % DIM] += 1.0
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


def build(tmp_path, pca=False):
    work, out = str(tmp_path / "work"), str(tmp_path / "vec")
    os.makedirs(os.path.join(work, "raw"))
    con = sqlite3.connect(os.path.join(work, "parts.sqlite"))
    con.execute("create table part(id integer primary key, text text, ntok integer)")
    con.execute("create table occ(part integer, key integer, a integer, b integer, src integer, primary key(part, key, a)) without rowid")
    for i, t in TEXTS.items():
        con.execute("insert into part values (?,?,?)", (i, t, len(t.split())))
    for p, k in OCC:
        con.execute("insert into occ values (?,?,?,?,?)", (p, k, 0, len(TEXTS[p].split()), 2))
    con.commit()
    enc = ToyEncoder()
    V = enc.encode([TEXTS[i] for i in TEXTS])
    mean = W = None
    if pca:
        rng = np.random.default_rng(0)
        mean = V.mean(0)
        W = np.linalg.qr(rng.normal(size=(DIM, 32)))[0].T.astype(np.float32)            # 32 orthonormal axes
        np.concatenate([mean, W.flatten()]).astype(np.float32).tofile(os.path.join(work, "pca.f32"))
        V = vec.project(V, mean, W)
    np.savez(os.path.join(work, "raw", "00000.npz"), i=np.arange(len(TEXTS)), v=V.astype(np.float16))
    info = parts.build_index(work, out, n_lists=2)
    os.makedirs(os.path.join(out, "model"))
    open(os.path.join(out, "model", "weights.bin"), "wb").write(b"x")
    con.close()
    import shutil
    shutil.copy(os.path.join(work, "parts.sqlite"), os.path.join(out, "parts.sqlite"))
    if pca:
        shutil.copy(os.path.join(work, "pca.f32"), os.path.join(out, "pca.f32"))
    json.dump(dict(info, unit="parts", model="toy", query_prefix="query: ", **({"pca": {"from": DIM, "to": 32}} if pca else {})),
              open(os.path.join(out, "meta.json"), "w"))
    return out


class ProjEncoder(ToyEncoder):
    pass


@pytest.fixture(params=[False, True], ids=["plain", "pca"])
def index(tmp_path, monkeypatch, request):
    out = build(tmp_path, pca=request.param)
    monkeypatch.setattr(config, "VEC_DIR", out)
    monkeypatch.setattr(semantic, "_index", None)
    monkeypatch.setattr(semantic, "_enc", ToyEncoder())
    semantic._cache.clear()
    yield out
    semantic._cache.clear()


def test_the_nearest_parts_and_where_they_stand(index):
    assert semantic.available()
    r = semantic.neighbours(["اقیموا الصلاه واتوا الزکاه"], k=3, nprobe=2)[0]
    assert r[0][0] == 0 and r[0][1] > 0.95
    assert semantic.neighbours(["یقیم الصلاه ویوتی الزکاه"], k=4, nprobe=2)[0][0][0] == 1
    assert semantic.neighbours(["اقیموا الصلاه"], k=3, nprobe=2, min_cos=0.999)[0] == []         # a cut-off
    got = semantic.parts_of([0, 4])
    assert got[0]["n"] == 2 and {o[0] for o in got[0]["occ"]} == {"v02:1", "v02:6"} and got[0]["text"] == TEXTS[0]
    assert got[4]["occ"] == [("kg:5", 0, 7, 2)]                                                # negative keys are the KG's
    assert semantic.parts_of([0], exclude={"v02:1"})[0]["occ"][0][0] == "v02:6" and semantic.parts_of([0], occ_max=1)[0]["n"] == 2
    h = semantic.hadith_hits(["اقیموا الصلاه واتوا الزکاه"], k=2)[0]
    assert {x[0] for x in h} >= {"v02:1", "v02:6"} and h[0][2] == [0, 4] and h[0][3] == 0     # one row per hadith: cosine, where, the part


def test_the_search_takes_hadiths_found_only_by_meaning(index, monkeypatch):
    pool, queries, flags = {}, [], []
    src = {"v02:1": dict(rec=dict(matn="اقیموا الصلاه واتوا الزکاه"))}
    own = lambda w: [(0, 0, 2), (1, 2, 4)]
    p = dict(search.DEFAULTS, sem=True, sem_min=0.3, sem_k_part=3, sem_k_all=3)
    search._semantic(src, own, ["v02:1"], p, pool, queries, flags)
    assert "v02:6" in pool and "q:sem" in pool["v02:6"]["signals"] and 0.3 <= pool["v02:6"]["sem"] <= 1.0
    assert "v02:1" not in pool                                             # a source hadith is not its own candidate
    assert queries and all(q["kind"] == "sem" for q in queries) and not flags
    hits = pool["v02:6"]["sem_hits"]                                      # which part of the sources matched, how well, where
    assert set(hits) <= {0, 1, -1} and all(0.3 <= cos <= 1.0 and loc == [0, 4] for cos, loc in hits.values())


def test_passages_are_distinct_part_texts_with_their_hadiths(index, monkeypatch):
    recs = {w: dict(wid=w, book="b", title="t", author="a", tradition="shia", vol=None, page=None, matn=TEXTS[p]) for p, w in
            [(0, "v02:1"), (0, "v02:6"), (1, "v02:2"), (2, "v02:3")]}
    monkeypatch.setattr(corpus, "get_many", lambda ws: {w: recs[w] for w in ws if w in recs})
    src = {"v02:9": dict(rec=dict(matn="اقیموا الصلاه واتوا"))}
    own = lambda w: [(0, 0, 3)]
    p = dict(search.DEFAULTS, sem_min=0.2, sem_k_part=3)
    queries, flags = [], []
    (q,) = search._sem_passages(src, own, ["v02:9"], p, queries, flags)
    assert q["clause"] == 0 and not flags and queries[0]["kind"] == "sem"
    top = q["variants"][0]
    assert top["id"] == 0 and top["n"] == 2 and {i["wid"] for i in top["items"]} == {"v02:1", "v02:6"} and top["items"][0]["span"] == [0, 4]
    assert [sh for _, sh in top["words"]][:2] == [True, True] and top["words"][-1][1] is False         # the words the part itself has
    assert [v["cos"] for v in q["variants"]] == sorted((v["cos"] for v in q["variants"]), reverse=True)
    # a text only the hadiths of step 1's list have is not offered
    src2 = {"v02:1": dict(rec=dict(matn="اقیموا الصلاه واتوا")), "v02:6": dict(rec=dict(matn="اقیموا الصلاه واتوا"))}
    (q2,) = search._sem_passages(src2, own, ["v02:1"], p, [], [])
    assert all(v["id"] != 0 for v in q2["variants"])


def test_picks_resolve_and_their_hadiths_join_the_pool(index):
    sem_parts = [dict(clause=0, variants=[dict(id=0, cos=0.9, n=2, items=[], words=[], text="x", ntok=4)]),
                 dict(clause=1, items=[dict(wid="v02:5", cos=0.8, first=3, copies=["v02:7"])])]               # one made by the first index
    got = search.resolve_picks(sem_parts, [["0", 0], ["0", 3], ["v02:5", 1], ["nope", 0]])
    assert got == [dict(id=0, clause=0, cos=0.9), dict(wid="v02:5", clause=1, cos=0.8, first=3, copies=["v02:7"])]
    pool, flags = {}, []
    search._picked({"v02:6": {}}, got, pool, search.DEFAULTS, flags)
    assert set(pool) == {"v02:1", "v02:5", "v02:7"} and "v02:6" not in pool                                  # every hadith that has the text, but the sources
    assert pool["v02:1"]["sem_hits"] == {0: (0.9, [0, 4])} and pool["v02:5"]["sem_hits"] == {1: (0.8, 3)} and "q:sem" in pool["v02:1"]["signals"]
    pool = {}
    search._picked({}, [dict(id=0, clause=0, cos=0.9)], pool, dict(search.DEFAULTS, sem_occ_max=1), flags)
    assert len(pool) == 1 and flags and flags[0]["kind"] == "sem_part_large"


def test_the_matching_passage_of_a_long_text_is_found_and_coloured():
    raw = "<b>" + " ".join(f"كلمة{i}" for i in range(100)) + "</b>"
    a, b = semantic.chunk_range(raw, 48)                                   # the chunk that starts at word 48 has 64 words
    text = search.strip_tags(raw)
    assert text[a:b].split()[0] == "كلمة48" and len(text[a:b].split()) == 52 and text[a:b].split()[-1] == "كلمة99"
    assert semantic.chunk_range(raw, 500) is None
    S = dict(seed=dict(wid="v02:9", matn="x"), clauses=[dict(i=0, span=[0, 1], members={"v02:9": [0, 1]})], tokens=["x"], cuts={},
             candidates=[dict(wid="v02:7", signals=["q:sem"], matn=raw, n_tokens=100, parts=[], sem_hits=[dict(clause=0, cos=0.8, first=48)])])
    m = search.with_marks(S)["candidates"][0]["marks"]
    assert m and m[0][2] == 0 and m[0][0] == a and m[-1][1] == b


def test_a_part_of_the_index_is_coloured_by_its_words():
    raw = "<b>" + " ".join(f"كلمة{i}" for i in range(20)) + "</b>"
    S = dict(seed=dict(wid="v02:9", matn="x"), clauses=[dict(i=0, span=[0, 1], members={"v02:9": [0, 1]})], tokens=["x"], cuts={},
             candidates=[dict(wid="v02:7", signals=["q:sem"], matn=raw, n_tokens=20, parts=[], sem_hits=[dict(clause=0, cos=0.8, span=[5, 9])])])
    m = search.with_marks(S)["candidates"][0]["marks"]
    t = search.strip_tags(raw)
    assert m and t[m[0][0]:m[-1][1]].split() == ["كلمة5", "كلمة6", "كلمة7", "كلمة8"] and m[0][2] == 0
    assert search._shown(raw * 1, [dict(clause=0, cos=0.8, span=[5, 9])]) == 1500


def test_a_broken_index_does_not_fail_the_search(index, monkeypatch):
    monkeypatch.setattr(semantic, "hadith_hits", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    flags = []
    search._semantic({"v02:1": dict(rec=dict(matn="اقیموا الصلاه"))}, lambda w: [(0, 0, 2)], ["v02:1"], dict(search.DEFAULTS, sem=True), {}, [], flags)
    assert flags and flags[0]["kind"] == "semantic_unavailable"
    monkeypatch.setattr(semantic, "neighbours", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    flags = []
    assert search._sem_passages({"v02:1": dict(rec=dict(matn="اقیموا الصلاه"))}, lambda w: [(0, 0, 2)], ["v02:1"], dict(search.DEFAULTS), [], flags) == [] and flags


def test_find_by_meaning_returns_records_with_the_part_coloured(index, monkeypatch):
    recs = {f"v02:{k}": dict(wid=f"v02:{k}", corpus="v02", hid=k, book="b", work="w", title="t", author="a", death=None, tradition="shia", vol=None,
                             page=None, no=None, matn=TEXTS[p], sanad="", translation="") for p, k in OCC if k > 0}
    recs["kg:5"] = dict(recs["v02:1"], wid="kg:5", corpus="kg", hid=5, matn=TEXTS[4])
    monkeypatch.setattr(corpus, "get_many", lambda w: {x: recs[x] for x in w if x in recs})
    r = semantic.find("اقیموا الصلاه واتوا الزکاه", "any", ["v02", "kg"], 5, 0)
    assert r["mode"] == "semantic" and r["hits"] and r["hits"][0]["score"] > 0.9
    assert r["hits"][0]["copies"] == ["v02:6"] or r["hits"][0]["wid"] == "v02:6"      # the identical text is one hit
    assert r["hits"][0]["marks"] and r["hits"][0]["marks"][0][:2] == [0, len(TEXTS[0])]
    assert all(h["wid"].startswith("kg:") for h in semantic.find("اقیموا", "any", ["kg"], 5, 0)["hits"])


def test_unavailable_index_is_said_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VEC_DIR", str(tmp_path / "none"))
    assert not semantic.available()
    r = semantic.find("x", "any", ["v02"], 5, 0)
    assert r["unavailable"] and r["hits"] == []


def test_vectors_are_projected_on_the_principal_axes():
    rng = np.random.default_rng(0)
    V = rng.normal(size=(5, 16)).astype(np.float32)
    mean, W = V.mean(0), np.linalg.qr(rng.normal(size=(16, 4)))[0].T.astype(np.float32)
    P = vec.project(V, mean, W)
    assert P.shape == (5, 4) and np.allclose(np.linalg.norm(P, axis=1), 1, atol=1e-5)
    assert vec.project(V, None, None) is V
    assert vec.emb_text("<b>قَالَ</b>  رَسُولُ") == "قال رسول"              # tags and diacritics go, the letters stay


def test_numpy_encoder_matches_torch(tmp_path):
    torch = pytest.importorskip("torch")
    tf = pytest.importorskip("transformers")
    pytest.importorskip("tokenizers")
    name = "intfloat/multilingual-e5-small"
    try:
        m = tf.AutoModel.from_pretrained(name, local_files_only=True).eval()
        tok = tf.AutoTokenizer.from_pretrained(name, local_files_only=True)
    except Exception:
        pytest.skip("model not in the local cache")
    from huggingface_hub import hf_hub_download
    try:
        hf_hub_download(name, "tokenizer.json", local_files_only=True)
    except Exception:
        pytest.skip("tokenizer.json not in the local cache")
    out = str(tmp_path / "m")
    vec.export_model(name, out, "mean")
    from icm.embed_np import NpEncoder
    texts = ["query: " + vec.emb_text(x) for x in ["إنما الأعمال بالنيات", "ستة لعنهم الله وكل نبي مجاب الدعوة"]]
    v = NpEncoder(out).encode(texts)
    with torch.no_grad():
        b = tok(texts, padding=True, return_tensors="pt")
        h = m(**b).last_hidden_state
        mk = b["attention_mask"].unsqueeze(-1).float()
        r = torch.nn.functional.normalize((h * mk).sum(1) / mk.sum(1), dim=-1).numpy()
    assert all(float(a @ b) > 0.999 for a, b in zip(v, r))


def test_passages_are_shown_as_distinct_texts_with_their_hadiths():
    it = lambda w, t, c: dict(wid=w, cos=c, first=0, text=t, cut_before=False, cut_after=False, copies=[], book="b", title="t", author="a", tradition="shia", vol=None, page=None)
    S = dict(clauses=[dict(i=0, text="اقیموا الصلاه", span=[0, 2], members={})],
             sem_parts=[dict(clause=0, items=[it("v02:1", "أَقِيمُوا الصَّلَاةَ وَآتُوا", 0.8), it("v02:2", "اقیموا الصلاه وآتوا", 0.9), it("v02:3", "یقیم الصلاه", 0.8)])])
    (q,) = search.sem_variants(S)
    assert [v["n"] for v in q["variants"]] == [2, 1]                                   # the same words in two spellings are one text, best cosine first
    top = q["variants"][0]
    assert top["cos"] == 0.9 and [i["wid"] for i in top["items"]] == ["v02:1", "v02:2"]
    assert [sh for _, sh in top["words"]] == [True, True, False]                       # the words the part itself has
