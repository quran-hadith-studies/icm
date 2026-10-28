"""The index of text parts (icm/build/parts.py): how hadiths are cut into parts, the same text kept once, and the whole build
(cut, PCA, vectors, index) on a handful of hadiths with a toy encoder."""
import os
import sys
import zlib
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from icm import config, hadithai, semantic
from icm.build import parts, vec
from icm.text import token_offsets, tokens

A = "ستة لعنهم الله وكل نبي مجاب، الزائد في كتاب الله، والمكذب بقدر الله، والتارك لسنتي"
B = "سِتَّةٌ لَعَنَهُمُ اللَّهُ وَكُلُّ نَبِيٍّ مُجَابٍ، الزَّائِدُ فِي كِتَابِ اللَّهِ، وَالْمُكَذِّبُ بِقَدَرِ اللَّهِ، وَالتَّارِكُ لِسُنَّتِي"      # A with diacritics
C = "اقيموا الصلاة واتوا الزكاة"                                                     # short: also one part as a whole
D = "قال رسول"                                                                         # under three words: no part
LONG = "، ".join(f"قال الإمام في المسألة رقم {w} كلاما طويلا" for w in "اب جد هو زح طي كل".split())


@pytest.fixture(autouse=True)
def no_hadithai(monkeypatch):
    monkeypatch.setattr(hadithai, "parts", lambda hid, toks: None)


def test_a_hadith_is_cut_by_the_rules_and_a_short_one_also_whole():
    tok, cuts = parts.cut_hadith(1, A)
    assert tok == tokens(A) and len(tok) == len(token_offsets(A))
    spans = [(a, b) for a, b, _ in cuts]
    assert all(b - a >= parts.MIN_TOK for a, b in spans) and len(spans) >= 3
    assert {s for _, _, s in cuts} <= {parts.SRC["rule"], parts.SRC["whole"]}                                    # no hadith.ai here
    tok, cuts = parts.cut_hadith(2, C)
    assert any((a, b) == (0, len(tok)) for a, b, _ in cuts)                                                      # a short hadith is one part as a whole
    assert parts.cut_hadith(3, D)[1] == []                                                                       # too short
    assert parts.cut_hadith(4, LONG)[1] and all(s != parts.SRC["whole"] for _, _, s in parts.cut_hadith(4, LONG)[1])   # a long one is not


def test_hadith_ai_segments_and_the_union_are_parts_too(monkeypatch):
    tok = tokens(A)
    monkeypatch.setattr(hadithai, "parts", lambda hid, toks: ([(0, 7), (7, len(toks))], [None, None]))
    _, cuts = parts.cut_hadith(5, A)
    got = {(a, b): s for a, b, s in cuts}
    assert got[(0, 7)] == parts.SRC["hai"]                                                                       # the segment, once, under hadith.ai
    assert (7, len(tok)) in got
    assert len(cuts) == len(got)                                                                                 # no span twice
    _, kg = parts.cut_hadith(-5, A)                                                                              # the KG has no hadith.ai parts
    assert all(s != parts.SRC["hai"] for _, _, s in kg)


def test_the_same_text_is_one_part_with_every_place_it_stands(tmp_path):
    work = str(tmp_path)
    n = parts.make_parts(work, None, procs=1, rows=[(1, A), (2, B), (3, C), (-4, D), (-5, C)])
    import sqlite3
    con = sqlite3.connect(os.path.join(work, "parts.sqlite"))
    assert con.execute("select count(*) from part").fetchone()[0] == n
    assert con.execute("select count(*) from part where ntok < 3").fetchone()[0] == 0
    rows = con.execute("select part, count(distinct key) from occ group by part").fetchall()
    by_part = {p: k for p, k in rows}
    assert max(by_part.values()) >= 2                                                                            # A and B (and C twice) share parts
    t = dict(con.execute("select id, text from part").fetchall())
    c_id = next(i for i, x in t.items() if "اقيموا" in x)
    keys = {k for (k,) in con.execute("select key from occ where part=?", (c_id,))}
    assert keys == {3, -5} and not any(k == -4 for (k,) in con.execute("select key from occ"))
    a_ids = {p for (p,) in con.execute("select part from occ where key=1")}
    assert a_ids == {p for (p,) in con.execute("select part from occ where key=2")}                              # diacritics make no new part
    assert parts.make_parts(work, None, procs=1, rows=[]) == n                                                    # done: not cut again


class FakeEnc:
    doc_prefix = ""
    kind = "cls"
    query_prefix = ""
    m = SimpleNamespace(config=SimpleNamespace(hidden_size=64))

    def encode(self, texts, prefix=""):
        out = np.zeros((len(texts), 64), dtype=np.float32)
        for j, t in enumerate(texts):
            t = vec.emb_text(t).replace(" ", "_")
            for i in range(len(t) - 2):
                out[j, zlib.crc32(t[i:i + 3].encode()) % 64] += 1.0
        return (out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)).astype(np.float16)


def test_the_whole_build_finds_a_part_from_another_spelling(tmp_path, monkeypatch):
    work, out = str(tmp_path / "w"), str(tmp_path / "vec")
    os.makedirs(work)
    n = parts.make_parts(work, None, procs=1, rows=[(1, A), (3, C), (-5, LONG)])
    enc = FakeEnc()
    mean, W = parts.fit_pca(work, enc, 16, n=1000)
    assert W.shape == (16, 64)
    chk = parts.pca_check(work, mean, W, nq=min(20, n), k=3)
    assert 0 <= chk["recall_at_k"] <= 1
    parts.embed_parts(work, enc, n, mean, W)
    z = np.load(os.path.join(work, "raw", "00000.npz"))
    assert z["v"].shape == (n, 16) and np.allclose(np.linalg.norm(z["v"].astype(np.float32), axis=1), 1, atol=2e-2)
    info = parts.build_index(work, out, 2)
    assert info == dict(n=n, dim=16, lists=2)
    import json, shutil
    for f in ("parts.sqlite", "pca.f32"):
        shutil.copy(os.path.join(work, f), os.path.join(out, f))
    os.makedirs(os.path.join(out, "model"))
    open(os.path.join(out, "model", "weights.bin"), "wb").write(b"x")
    json.dump(dict(info, unit="parts", model="toy", query_prefix="", pca={"from": 64, "to": 16}), open(os.path.join(out, "meta.json"), "w"))
    monkeypatch.setattr(config, "VEC_DIR", out)
    monkeypatch.setattr(semantic, "_index", None)
    monkeypatch.setattr(semantic, "_enc", FakeEnc())
    semantic._cache.clear()
    hits = semantic.hadith_hits(["اقيموا الصلاة واتوا الزكاة"], k=3)[0]
    assert hits and hits[0][0] == "v02:3" and hits[0][1] > 0.9
