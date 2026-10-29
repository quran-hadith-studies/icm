"""Shared pieces of the semantic index (the index itself is built by ``icm.build.parts``): the text a model reads
(``emb_text``), the corpus-side encoder (``TorchEncoder``), spherical k-means, the PCA projection of the vectors
(``project``) and the encoder export for the server (``export_model``)."""
from __future__ import annotations

import json
import os
import re
import shutil
from typing import List

import numpy as np

from ..text import strip_tags

MAX_LEN = 256                 # tokens
BATCH = 64
_HAR = re.compile("[ً-ٰٟـۖ-ۭ]")
_PAIRS = (("(", ")"), ("[", "]"), ("{", "}"), ("«", "»"), ("﴿", "﴾"))
_EDGE = re.compile(r"^[^\w]+|[^\w]+$")            # what is not a letter or digit at either end: «-», ««», «[», «:», «،», «.»


def emb_text(raw: str) -> str:
    """The text a model reads: tags, diacritics and tatweel removed, spaces collapsed, punctuation at either end trimmed (a part
    is cut out of a matn by words, so it carries the dash or the colon glued to its first or last word: that moved the vector by
    cos 0.96 on average, 0.68 at worst, and which hadith's wording a part was kept from was accidental); the letters stay as
    written (the models were trained on ordinary Arabic, not on ``norm``'s Persian-letter spelling) and so does the punctuation inside."""
    t = _HAR.sub("", strip_tags(raw or "")).replace("‌", " ")
    t = re.sub(r"\s+", " ", t).strip()
    c = _EDGE.sub("", t)
    if not c:
        return t                                      # a text of nothing but punctuation stays as it is
    for o, q in _PAIRS:                               # a bracket or a quote opened inside the text keeps its closing one («قال «لا» »)
        if c.count(o) > c.count(q) and q in t[len(t.rstrip(q + "،.:؛!؟ ")):]:
            c += q
        elif c.count(q) > c.count(o) and o in t[:len(t) - len(t.lstrip(o + " "))]:
            c = o + c
    return c


def project(V: np.ndarray, mean: np.ndarray, W: np.ndarray) -> np.ndarray:
    """Vectors (n, 1024) -> unit vectors (n, 256): centre, rotate onto the first principal axes, normalise. ``mean`` None: as they are."""
    if mean is None:
        return V
    X = (np.asarray(V, dtype=np.float32) - mean) @ W.T
    return X / np.maximum(np.linalg.norm(X, axis=-1, keepdims=True), 1e-12)


# ---------------------------------------------------------------------------
class TorchEncoder:
    """The corpus-side encoder (torch). ``kind``: e5 (mean pooling, query:/passage: prefixes), cls (CLS pooling)."""

    def __init__(self, name: str):
        import torch
        from transformers import AutoModel, AutoTokenizer
        self.torch = torch
        self.name = name
        self.dev = "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"
        self.tok = AutoTokenizer.from_pretrained(name)
        self.m = AutoModel.from_pretrained(name, dtype=torch.float16 if self.dev != "cpu" else torch.float32).to(self.dev).eval()
        self.kind = "e5" if "e5" in name else "cls"
        self.doc_prefix = "passage: " if self.kind == "e5" else ""
        self.query_prefix = "query: " if self.kind == "e5" else ""

    def encode(self, texts: List[str], prefix: str = "") -> np.ndarray:
        torch = self.torch
        with torch.no_grad():
            b = self.tok([prefix + t for t in texts], padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt").to(self.dev)
            h = self.m(**b).last_hidden_state
            if self.kind == "e5":
                m = b["attention_mask"].unsqueeze(-1).to(h.dtype)
                v = (h * m).sum(1) / m.sum(1)
            else:
                v = h[:, 0]
            return torch.nn.functional.normalize(v.float(), dim=-1).cpu().numpy().astype(np.float16)



# ---------------------------------------------------------------------------
# k-means
# ---------------------------------------------------------------------------
def kmeans(x: np.ndarray, k: int, iters: int = 20, seed: int = 0) -> np.ndarray:
    """Spherical k-means (unit vectors, cosine)."""
    import torch
    dev = "mps" if torch.backends.mps.is_available() else "cpu"
    rng = np.random.default_rng(seed)
    c = torch.tensor(x[rng.choice(len(x), k, replace=False)], device=dev, dtype=torch.float32)
    X = torch.tensor(x, device=dev, dtype=torch.float32)
    for it in range(iters):
        a = torch.cat([(X[s:s + 50000] @ c.T).argmax(1) for s in range(0, len(X), 50000)])
        s_ = torch.zeros_like(c).index_add_(0, a, X)
        cnt = torch.bincount(a, minlength=k)
        empty = cnt == 0
        s_[empty] = X[torch.randint(0, len(X), (int(empty.sum()),), device=dev)] if empty.any() else s_[empty]
        c = torch.nn.functional.normalize(s_, dim=-1)
        print(f"  kmeans {it + 1}/{iters}", flush=True)
    return c.cpu().numpy()


def export_model(name: str, out: str, pooling: str) -> None:
    """``weights.bin`` (float16 tensors of the encoder, laid end to end) + ``weights.json`` {tensor: [offset, shape]},
    ``model.json`` (sizes, pooling, prefixes) and the tokenizer, for ``icm.embed_np.NpEncoder``."""
    import torch
    from huggingface_hub import hf_hub_download
    from transformers import AutoConfig, AutoModel
    os.makedirs(out, exist_ok=True)
    m = AutoModel.from_pretrained(name, dtype=torch.float16)
    cfg = AutoConfig.from_pretrained(name)
    idx, off = {}, 0
    with open(os.path.join(out, "weights.bin"), "wb") as f:
        for k, t in m.state_dict().items():
            if k.startswith("pooler") or "position_ids" in k:
                continue
            a = t.detach().cpu().numpy().astype(np.float16)
            f.write(a.tobytes())
            idx[k] = [off, list(a.shape)]
            off += a.nbytes
    json.dump(idx, open(os.path.join(out, "weights.json"), "w"))
    kind = "e5" if "e5" in name else "cls"
    json.dump(dict(hidden=cfg.hidden_size, layers=cfg.num_hidden_layers, heads=cfg.num_attention_heads, eps=cfg.layer_norm_eps,
                   pad_id=cfg.pad_token_id, arch="xlmr" if cfg.model_type == "xlm-roberta" else "bert", pooling=pooling, max_len=MAX_LEN,
                   query_prefix="query: " if kind == "e5" else "", model=name), open(os.path.join(out, "model.json"), "w"))
    shutil.copy(hf_hub_download(name, "tokenizer.json"), os.path.join(out, "tokenizer.json"))
