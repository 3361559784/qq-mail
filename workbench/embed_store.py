from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import requests

from workbench.db import WorkbenchDB
from workbench.models import utc_now_iso
from workbench.normalize import stable_content_hash

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None


class EmbeddingClient:
    def __init__(
        self,
        token: str,
        api_url: str,
        model: str,
        timeout_seconds: int = 30,
        request_fn: Callable[..., Any] = requests.post,
    ) -> None:
        self.token = token
        self.api_url = api_url
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.request_fn = request_fn

    @staticmethod
    def _fallback_embedding(text: str, dim: int = 256) -> list[float]:
        out = [0.0] * dim
        for idx, ch in enumerate(text):
            out[idx % dim] += (ord(ch) % 97) / 100.0
        norm = sum(x * x for x in out) ** 0.5
        if norm > 0:
            out = [x / norm for x in out]
        return out

    def embed(self, text: str) -> list[float]:
        if not self.token.strip():
            return self._fallback_embedding(text)

        payload = {
            "model": self.model,
            "input": text,
        }
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        response = self.request_fn(self.api_url, headers=headers, json=payload, timeout=self.timeout_seconds)
        status = getattr(response, "status_code", None)
        if status is None or status >= 400:
            return self._fallback_embedding(text)

        data = response.json()
        vec = None
        if isinstance(data, dict):
            entries = data.get("data")
            if isinstance(entries, list) and entries:
                first = entries[0]
                if isinstance(first, dict):
                    vec = first.get("embedding")
            if vec is None and isinstance(data.get("embedding"), list):
                vec = data.get("embedding")

        if not isinstance(vec, list) or not vec:
            return self._fallback_embedding(text)

        out: list[float] = []
        for item in vec:
            try:
                out.append(float(item))
            except Exception:
                out.append(0.0)
        return out


def to_blob(vector: list[float]) -> tuple[bytes, int]:
    arr = np.asarray(vector, dtype=np.float32)
    return arr.tobytes(), int(arr.shape[0])


def from_blob(blob: bytes, dim: int) -> np.ndarray:
    arr = np.frombuffer(blob, dtype=np.float32)
    if dim > 0 and arr.shape[0] != dim:
        arr = arr[:dim] if arr.shape[0] > dim else np.pad(arr, (0, dim - arr.shape[0]))
    return arr.astype(np.float32)


def upsert_embedding_for_mail(
    db: WorkbenchDB,
    client: EmbeddingClient,
    mail_row,
) -> bool:
    mail_id = int(mail_row["id"])
    content_hash = stable_content_hash(
        subject=str(mail_row["subject"] or ""),
        sender_email=str(mail_row["sender_email"] or ""),
        body_text=str(mail_row["body_text"] or ""),
    )

    meta = db.get_embedding_meta(mail_id)
    if meta and str(meta["content_hash"]) == content_hash and str(meta["model"]) == client.model:
        return False

    text = f"Subject: {mail_row['subject']}\nFrom: {mail_row['sender_email']}\n{mail_row['body_text']}"
    vector = client.embed(text)
    blob, dim = to_blob(vector)
    db.upsert_embedding(
        mail_id=mail_id,
        content_hash=content_hash,
        model=client.model,
        vector=blob,
        vector_dim=dim,
        faiss_pos=-1,
        updated_at_utc=utc_now_iso(),
    )
    return True


def rebuild_faiss_from_sqlite(db: WorkbenchDB, model: str, index_path: Path) -> int:
    rows = db.list_embeddings(model=model)
    if not rows:
        if index_path.exists():
            index_path.unlink()
        if index_path.with_suffix(index_path.suffix + ".npy").exists():
            index_path.with_suffix(index_path.suffix + ".npy").unlink()
        db.set_state("faiss_index_version", utc_now_iso())
        return 0

    vectors: list[np.ndarray] = []
    mapping: list[tuple[int, int]] = []
    for pos, row in enumerate(rows):
        vec = from_blob(row["vector_blob"], int(row["vector_dim"]))
        vectors.append(vec)
        mapping.append((int(row["mail_id"]), pos))

    matrix = np.vstack(vectors).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms

    if faiss is not None:
        idx = faiss.IndexFlatIP(matrix.shape[1])
        idx.add(matrix)
        faiss.write_index(idx, str(index_path))
    else:
        np.save(index_path.with_suffix(index_path.suffix + ".npy"), matrix)
        index_path.write_text(json.dumps({"fallback": "numpy", "dim": int(matrix.shape[1])}), encoding="utf-8")

    db.update_faiss_positions(model=model, positions=mapping)
    db.set_state("faiss_index_version", utc_now_iso())
    return int(matrix.shape[0])


def search_vectors(db: WorkbenchDB, model: str, query_vector: list[float], index_path: Path, top_k: int) -> list[tuple[int, float]]:
    rows = db.list_embeddings(model=model)
    if not rows:
        return []

    if faiss is not None and index_path.exists():
        idx = faiss.read_index(str(index_path))
        q = np.asarray(query_vector, dtype=np.float32)
        q_norm = np.linalg.norm(q)
        if q_norm > 0:
            q = q / q_norm
        scores, positions = idx.search(q.reshape(1, -1), max(top_k, 1))
        pos_to_mail = {int(row["faiss_pos"]): int(row["mail_id"]) for row in rows if int(row["faiss_pos"]) >= 0}
        hits: list[tuple[int, float]] = []
        for pos, score in zip(positions[0], scores[0]):
            p = int(pos)
            if p < 0:
                continue
            mail_id = pos_to_mail.get(p)
            if mail_id is None:
                continue
            hits.append((mail_id, float(score)))
        return hits

    # numpy fallback
    vectors: list[np.ndarray] = []
    mail_ids: list[int] = []
    for row in rows:
        vectors.append(from_blob(row["vector_blob"], int(row["vector_dim"])))
        mail_ids.append(int(row["mail_id"]))
    matrix = np.vstack(vectors).astype(np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    matrix = matrix / norms

    q = np.asarray(query_vector, dtype=np.float32)
    q_norm = np.linalg.norm(q)
    if q_norm > 0:
        q = q / q_norm

    scores = matrix @ q
    order = np.argsort(scores)[::-1][: max(top_k, 1)]
    return [(mail_ids[int(idx)], float(scores[int(idx)])) for idx in order]


def embedding_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
