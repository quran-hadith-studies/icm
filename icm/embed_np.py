"""A BERT / XLM-RoBERTa sentence encoder in numpy, for the one thing the server has to do with a neural model: embed a
query (a few words to a few lines) with the model the corpus was embedded with. No torch (the server has 1 GB of RAM and
numpy only): the weights are a memory-mapped file of float16 tensors (``weights.bin`` + ``weights.json``, written by
``icm.build.vec.export_model``) and are read as needed, so the resident size is the working set of one forward pass,
and the tokenizer is ``tokenizers`` (``tokenizer.json``).

    enc = NpEncoder("<dir>")
    enc.encode(["query: ..."])    # -> float32 (n, hidden), unit length

Matches the HF model's own output (tests: cosine > 0.999 against torch when both are available).
"""
from __future__ import annotations

import json
import os
from typing import List, Sequence

import numpy as np


def _gelu(x: np.ndarray) -> np.ndarray:
    from math import sqrt
    # exact (erf) gelu without scipy: erf by the Abramowitz-Stegun 7.1.26 formula would lose digits; use np's vectorised erf via math
    return 0.5 * x * (1.0 + _erf(x / sqrt(2.0)))


def _erf(x: np.ndarray) -> np.ndarray:
    # float32-accurate erf: Numerical Recipes erfc Chebyshev fit (|err| < 1.2e-7)
    z = np.abs(x)
    t = 1.0 / (1.0 + 0.5 * z)
    r = t * np.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 + t * (
        0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))))
    return np.where(x >= 0, 1.0 - r, r - 1.0)


class NpEncoder:
    def __init__(self, path: str):
        self.path = path
        self.cfg = json.load(open(os.path.join(path, "model.json"), encoding="utf-8"))
        idx = json.load(open(os.path.join(path, "weights.json"), encoding="utf-8"))
        blob = os.path.join(path, "weights.bin")
        self.w = {k: np.memmap(blob, dtype=np.float16, mode="r", offset=o, shape=tuple(s)) for k, (o, s) in idx.items()}
        from tokenizers import Tokenizer
        self.tok = Tokenizer.from_file(os.path.join(path, "tokenizer.json"))
        self.tok.no_padding()
        self.max_len = int(self.cfg.get("max_len", 256))
        self.tok.enable_truncation(self.max_len)

    def _f(self, name: str) -> np.ndarray:
        return np.asarray(self.w[name], dtype=np.float32)

    def _ln(self, x, name, eps):
        g, b = self._f(name + ".weight"), self._f(name + ".bias")
        m = x.mean(-1, keepdims=True)
        v = ((x - m) ** 2).mean(-1, keepdims=True)
        return (x - m) / np.sqrt(v + eps) * g + b

    def _lin(self, x, name):
        return x @ self._f(name + ".weight").T + self._f(name + ".bias")

    def _forward(self, ids: np.ndarray, mask: np.ndarray) -> np.ndarray:
        """``ids``, ``mask``: (batch, length); returns (batch, length, hidden). Weights are read and widened once per layer
        for the whole batch, so several texts cost little more than one."""
        c, p = self.cfg, "encoder.layer.%d."
        H, nh, eps = c["hidden"], c["heads"], c.get("eps", 1e-5)
        B, n = ids.shape
        pad = c.get("pad_id", 1)
        pos = (np.cumsum(mask, 1) * mask + pad) if c.get("arch", "xlmr") == "xlmr" else np.broadcast_to(np.arange(n), (B, n))
        x = np.asarray(self.w["embeddings.word_embeddings.weight"][ids.reshape(-1)], dtype=np.float32).reshape(B, n, H) \
            + np.asarray(self.w["embeddings.position_embeddings.weight"][pos.reshape(-1)], dtype=np.float32).reshape(B, n, H) \
            + self._f("embeddings.token_type_embeddings.weight")[0]
        x = self._ln(x, "embeddings.LayerNorm", eps)
        dh = H // nh
        bias = np.where(mask[:, None, None, :] > 0, 0.0, -1e9).astype(np.float32)
        for i in range(c["layers"]):
            a = p % i + "attention."
            q = self._lin(x, a + "self.query").reshape(B, n, nh, dh).transpose(0, 2, 1, 3)
            k = self._lin(x, a + "self.key").reshape(B, n, nh, dh).transpose(0, 2, 1, 3)
            v = self._lin(x, a + "self.value").reshape(B, n, nh, dh).transpose(0, 2, 1, 3)
            s = q @ k.transpose(0, 1, 3, 2) / np.sqrt(dh) + bias
            s = np.exp(s - s.max(-1, keepdims=True))
            s /= s.sum(-1, keepdims=True)
            ctx = (s @ v).transpose(0, 2, 1, 3).reshape(B, n, H)
            x = self._ln(x + self._lin(ctx, a + "output.dense"), a + "output.LayerNorm", eps)
            h = _gelu(self._lin(x, p % i + "intermediate.dense"))
            x = self._ln(x + self._lin(h, p % i + "output.dense"), p % i + "output.LayerNorm", eps)
        return x

    def encode(self, texts: Sequence[str], batch: int = 8) -> np.ndarray:
        out = np.zeros((len(texts), self.cfg["hidden"]), dtype=np.float32)
        toks = [self.tok.encode(t).ids for t in texts]
        order = sorted(range(len(texts)), key=lambda i: len(toks[i]))
        pad = self.cfg.get("pad_id", 1)
        for s in range(0, len(order), batch):
            idx = order[s:s + batch]
            n = max(len(toks[i]) for i in idx)
            ids = np.full((len(idx), n), pad, dtype=np.int64)
            mask = np.zeros((len(idx), n), dtype=np.int64)
            for r, i in enumerate(idx):
                ids[r, :len(toks[i])] = toks[i]
                mask[r, :len(toks[i])] = 1
            h = self._forward(ids, mask)
            if self.cfg.get("pooling", "mean") == "mean":
                m = mask[:, :, None].astype(np.float32)
                v = (h * m).sum(1) / m.sum(1)
            else:
                v = h[:, 0]
            out[idx] = v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
        return out
