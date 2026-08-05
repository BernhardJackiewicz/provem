"""Key-gated dense-retrieval support: embeddings provider, disk cache, RRF fusion.

The default Engram pipeline stays stdlib-only; nothing here is imported unless a
caller opts in. The measured motivation (LoCoMo loss taxonomy vs Mem0): 30% of
losses are paraphrase/attribute-colocation gaps that lexical BM25 cannot bridge
("favorite pizza" -> a turn that only says "pepperoni"). A dense channel fused
with BM25 via Reciprocal Rank Fusion attacks exactly that gap.

Design constraints honored:
- deterministic + cheap to iterate: every embedding is cached on disk keyed by
  sha256(model|text); re-runs never re-pay and never drift.
- provider is injected; OpenAIEmbeddingProvider is a stdlib urllib client so no
  dependency is added. Tests use a mock provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


def _key(model: str, text: str) -> str:
    return hashlib.sha256(("%s|%s" % (model, text)).encode("utf-8")).hexdigest()


class DiskVectorCache:
    """Append-only JSONL vector cache; loads fully into memory on open."""

    def __init__(self, path: Optional[str]):
        self.path = path
        self._lock = threading.Lock()
        self._data: Dict[str, List[float]] = {}
        if path and os.path.exists(path):
            with open(path) as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                        self._data[row["k"]] = row["v"]
                    except (ValueError, KeyError):
                        continue

    def __len__(self) -> int:
        return len(self._data)

    def get(self, key: str) -> Optional[List[float]]:
        return self._data.get(key)

    def put(self, key: str, vector: Sequence[float]) -> None:
        with self._lock:
            if key in self._data:
                return
            rounded = [round(float(v), 5) for v in vector]
            self._data[key] = rounded
            if self.path:
                with open(self.path, "a") as handle:
                    handle.write(json.dumps({"k": key, "v": rounded}) + "\n")

    def delete_keys(self, keys: Sequence[str]) -> int:
        """Delete cached vectors and compact the on-disk file atomically.

        The erasure-derivative path: an erased text's vector must not persist
        on disk forever just because the cache format is append-only.
        """
        with self._lock:
            removed = 0
            for key in keys:
                if self._data.pop(key, None) is not None:
                    removed += 1
            if removed and self.path:
                tmp_path = self.path + ".tmp"
                with open(tmp_path, "w") as handle:
                    for key, vector in self._data.items():
                        handle.write(json.dumps({"k": key, "v": vector}) + "\n")
                os.replace(tmp_path, self.path)
            return removed

    def delete_texts(self, model: str, texts: Sequence[str]) -> int:
        # Keys are content hashes; recompute from text instead of keeping a
        # separate record-id map (no new state to migrate or leak).
        return self.delete_keys([_key(model, text) for text in texts])


class OpenAIEmbeddingProvider:
    """Minimal stdlib client for the OpenAI embeddings endpoint."""

    def __init__(self, model: str = "text-embedding-3-small", api_key: Optional[str] = None,
                 batch_size: int = 100, retries: int = 5):
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        if not self.api_key:
            raise ValueError("OpenAIEmbeddingProvider requires OPENAI_API_KEY")
        self.batch_size = batch_size
        self.retries = retries

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        vectors: List[List[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start:start + self.batch_size])
            vectors.extend(self._embed_batch(batch))
        return vectors

    def _embed_batch(self, batch: List[str]) -> List[List[float]]:
        body = json.dumps({"model": self.model, "input": batch}).encode("utf-8")
        last = None
        for attempt in range(self.retries):
            request = urllib.request.Request(
                "https://api.openai.com/v1/embeddings", data=body,
                headers={"Authorization": "Bearer %s" % self.api_key,
                         "Content-Type": "application/json"},
                method="POST")
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                rows = sorted(payload["data"], key=lambda item: item["index"])
                return [row["embedding"] for row in rows]
            except urllib.error.HTTPError as error:
                last = "%s %s" % (error.code, error.read().decode("utf-8", "ignore")[:160])
                if error.code in (429, 500, 502, 503):
                    time.sleep(min(2 ** attempt, 20))
                    continue
                raise RuntimeError("embeddings HTTP %s" % last)
            except (urllib.error.URLError, TimeoutError) as error:
                last = str(error)
                time.sleep(min(2 ** attempt, 20))
        raise RuntimeError("embeddings failed after retries: %s" % last)


class CachedEmbedder:
    """Provider + cache: only uncached texts hit the API; order is preserved."""

    def __init__(self, provider, cache: DiskVectorCache, model: Optional[str] = None):
        self.provider = provider
        self.cache = cache
        self.model = model or getattr(provider, "model", "unknown")

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        keys = [_key(self.model, text) for text in texts]
        missing = [(index, text) for index, (key, text) in enumerate(zip(keys, texts))
                   if self.cache.get(key) is None]
        if missing:
            fresh = self.provider.embed([text for _, text in missing])
            for (index, _), vector in zip(missing, fresh):
                self.cache.put(keys[index], vector)
        return [list(self.cache.get(key)) for key in keys]

    def purge_texts(self, texts: Sequence[str]) -> int:
        """Delete these texts' cached vectors (erasure derivative sweep)."""
        return self.cache.delete_texts(self.model, texts)


class VectorCacheDerivativeStore:
    """DerivativeStore over a CachedEmbedder: purges erased records' text
    vectors from the cache during forget()/cleanup_expired() sweeps."""

    name = "vector_cache"

    def __init__(self, embedder: CachedEmbedder):
        self.embedder = embedder

    def purge_records(self, records: Sequence[object]) -> int:
        texts = [getattr(record, "text", "") for record in records]
        return self.embedder.purge_texts([text for text in texts if text])


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def rrf_fuse(rankings: Iterable[Sequence[str]], k: float = 60.0) -> List[Tuple[str, float]]:
    """Reciprocal Rank Fusion over id rankings; higher fused score = better.

    Standard RRF (Cormack et al.): score(d) = sum over rankings 1/(k + rank_d).
    Ids missing from a ranking simply contribute nothing for it.
    """
    scores: Dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))
