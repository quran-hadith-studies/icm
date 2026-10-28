"""Semantic search: the index (icm/build/vec.py's build_index, icm/semantic.py) over a tiny corpus with a toy encoder, the
numpy encoder against torch when both are there, and how the search takes hadiths found by meaning into its candidates."""
import json
import os
import sqlite3
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, corpus, search, semantic
from icm.build import vec

TEXTS = {                                      # key -> text (negative: the KG)
    1: "اقیموا الصلاه واتوا الزکاه",
    2: "یقیم الصلاه ویوتی الزکاه",
    3: "ان الله لا یغفر ان یشرک به",
    4: "من کنت مولاه فهذا علی مولاه",
    -5: "یغفر الله ما دون ذلک لمن یشاء",
    6: "اقیموا الصلاه واتوا الزکاه",          # the text of 1 again (another hadith): a duplicate
}
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


def build(tmp_path):
    work, out = str(tmp_path / "work"), str(tmp_path / "vec")
    os.makedirs(os.path.join(work, "raw"))
    con = sqlite3.connect(os.path.join(work, "chunks.sqlite"))
    con.execute("create table ch(i integer primary key, key integer, cstart integer, nwords integer, text text)")
    con.execute("create table dup(key integer, canon integer)")
    keys = [k for k in TEXTS if k != 6]
    for i, k in enumerate(keys):
        con.execute("insert into ch values (?,?,?,?,?)", (i, k, 0, len(TEXTS[k].split()), TEXTS[k]))
    con.execute("insert into dup values (6, 1)")
    con.commit()
    enc = ToyEncoder()
    V = enc.encode([TEXTS[k] for k in keys]).astype(np.float16)
    np.savez(os.path.join(work, "raw", "00000.npz"), i=np.arange(len(keys)), v=V)
    info = vec.build_index(work, out, n_lists=2)
    os.makedirs(os.path.join(out, "model"))
    open(os.path.join(out, "model", "weights.bin"), "wb").write(b"x")
    json.dump(dict(info, model="toy", query_prefix="query: "), open(os.path.join(out, "meta.json"), "w"))
    return out


@pytest.fixture
def index(tmp_path, monkeypatch):
    out = build(tmp_path)
    monkeypatch.setattr(config, "VEC_DIR", out)
    monkeypatch.setattr(semantic, "_index", None)
    monkeypatch.setattr(semantic, "_enc", ToyEncoder())
    semantic._cache.clear()
    yield out
    semantic._cache.clear()


def test_the_nearest_hadith_by_meaning_and_its_duplicates(index):
    assert semantic.available()
    r = semantic.neighbours(["اقیموا الصلاه واتوا الزکاه"], k=3, nprobe=2)[0]
    assert r[0][0] in ("v02:1", "v02:6") and r[0][1] > 0.95
    assert {"v02:1", "v02:6"} <= {w for w, _ in r}                      # a hadith with the same text is returned with it
    names = [w for w, _ in semantic.neighbours(["یقیم الصلاه ویوتی الزکاه"], k=4, nprobe=2)[0]]
    assert names[0] == "v02:2"
    r = semantic.neighbours(["یغفر الله ما دون ذلک"], k=2, nprobe=2)[0]
    assert r[0][0] == "kg:5"                                              # negative keys are the KG's
    assert semantic.neighbours(["اقیموا الصلاه"], k=3, nprobe=2, min_cos=0.999)[0] == []     # a cut-off


def test_the_search_takes_hadiths_found_only_by_meaning(index, monkeypatch):
    pool, queries, flags = {}, [], []
    src = {"v02:1": dict(rec=dict(matn="اقیموا الصلاه واتوا الزکاه"))}
    own = lambda w: [(0, 0, 2), (1, 2, 4)]
    p = dict(search.DEFAULTS, sem=True, sem_min=0.3, sem_k_part=3, sem_k_all=3)
    search._semantic(src, own, ["v02:1"], p, pool, queries, flags)
    assert "v02:2" in pool and "q:sem" in pool["v02:2"]["signals"] and 0.3 <= pool["v02:2"]["sem"] <= 1.0
    assert "v02:1" not in pool                                             # a source hadith is not its own candidate
    assert queries and all(q["kind"] == "sem" for q in queries) and not flags
    hits = pool["v02:2"]["sem_hits"]                                      # which part of the sources matched, how well, where
    assert set(hits) <= {0, 1, -1} and all(0.3 <= cos <= 1.0 and first == 0 for cos, first in hits.values())


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


def test_a_broken_index_does_not_fail_the_search(index, monkeypatch):
    monkeypatch.setattr(semantic, "neighbours", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    flags = []
    search._semantic({"v02:1": dict(rec=dict(matn="اقیموا الصلاه"))}, lambda w: [(0, 0, 2)], ["v02:1"], dict(search.DEFAULTS, sem=True), {}, [], flags)
    assert flags and flags[0]["kind"] == "semantic_unavailable"


def test_find_by_meaning_returns_records(index, monkeypatch):
    recs = {f"v02:{k}": dict(wid=f"v02:{k}", corpus="v02", hid=k, book="b", work="w", title="t", author="a", death=None, tradition="shia", vol=None,
                             page=None, no=None, matn=t, sanad="", translation="") for k, t in TEXTS.items() if k > 0}
    recs["kg:5"] = dict(recs["v02:3"], wid="kg:5", corpus="kg", hid=5, matn=TEXTS[-5])
    monkeypatch.setattr(corpus, "get_many", lambda w: {x: recs[x] for x in w if x in recs})
    r = semantic.find("اقیموا الصلاه واتوا الزکاه", "any", ["v02", "kg"], 5, 0)
    assert r["mode"] == "semantic" and r["hits"] and r["hits"][0]["score"] > 0.9
    assert r["hits"][0]["copies"] == ["v02:6"] or r["hits"][0]["wid"] == "v02:6"      # the identical text is one hit
    assert all(h["wid"].startswith("kg:") for h in semantic.find("اقیموا", "any", ["kg"], 5, 0)["hits"])


def test_unavailable_index_is_said_not_raised(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VEC_DIR", str(tmp_path / "none"))
    assert not semantic.available()
    r = semantic.find("x", "any", ["v02"], 5, 0)
    assert r["unavailable"] and r["hits"] == []


def test_chunks_cut_long_texts_in_overlapping_windows():
    t = " ".join(f"w{i}" for i in range(150))
    ch = vec.chunk_text(t)
    assert ch[0][0] == 0 and ch[1][0] == vec.STRIDE_WORDS and all(len(c.split()) <= vec.CHUNK_WORDS for _, c in ch)
    assert ch[-1][1].endswith("w149")                                      # the end is covered
    assert vec.chunk_text("a b c") == [(0, "a b c")] and vec.chunk_text("") == []
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
