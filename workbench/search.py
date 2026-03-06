from __future__ import annotations

from typing import Any, Callable

import requests

from workbench.db import WorkbenchDB
from workbench.embed_store import EmbeddingClient, search_vectors
from workbench.models import QaAnswer, SearchHit


class SearchService:
    def __init__(
        self,
        db: WorkbenchDB,
        embedding_client: EmbeddingClient,
        index_path,
        llm_token: str,
        llm_api_url: str,
        llm_model: str,
        request_fn: Callable[..., Any] = requests.post,
    ) -> None:
        self.db = db
        self.embedding_client = embedding_client
        self.index_path = index_path
        self.llm_token = llm_token
        self.llm_api_url = llm_api_url
        self.llm_model = llm_model
        self.request_fn = request_fn

    def _search_hits(self, query: str, top_k: int) -> list[SearchHit]:
        qvec = self.embedding_client.embed(query)
        scored = search_vectors(
            db=self.db,
            model=self.embedding_client.model,
            query_vector=qvec,
            index_path=self.index_path,
            top_k=top_k,
        )
        mail_map = self.db.get_mails_by_ids([mail_id for mail_id, _ in scored])

        hits: list[SearchHit] = []
        for mail_id, score in scored:
            row = mail_map.get(mail_id)
            if row is None:
                continue
            body = str(row["body_text"] or "")
            snippet = body[:240] + ("..." if len(body) > 240 else "")
            hits.append(
                SearchHit(
                    mail_id=mail_id,
                    subject=str(row["subject"] or ""),
                    sender=str(row["sender_email"] or ""),
                    received_at_utc=str(row["received_at_utc"] or ""),
                    snippet=snippet,
                    score=score,
                )
            )
        return hits

    def _answer_with_llm(self, query: str, hits: list[SearchHit]) -> str:
        if not self.llm_token.strip() or not hits:
            return ""

        evidence_text = "\n\n".join(
            [
                f"[Hit {idx}] subject={hit.subject}; sender={hit.sender}; date={hit.received_at_utc}\n{hit.snippet}"
                for idx, hit in enumerate(hits, start=1)
            ]
        )
        prompt = (
            "Answer user's question based on provided email evidence."
            "Return concise answer in Chinese, and avoid fabricating unknown facts.\n\n"
            f"Question: {query}\n\nEvidence:\n{evidence_text}"
        )

        payload = {
            "model": self.llm_model,
            "messages": [
                {"role": "system", "content": "You are an evidence-grounded assistant."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 300,
            "stream": False,
        }
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.llm_token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        }
        response = self.request_fn(self.llm_api_url, headers=headers, json=payload, timeout=30)
        if getattr(response, "status_code", 0) >= 400:
            return ""
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            return ""
        message = choices[0].get("message") or {}
        content = message.get("content", "")
        if isinstance(content, list):
            text_parts: list[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    text_parts.append(str(part.get("text", "")))
            content = "\n".join(text_parts)
        return str(content).strip()

    def answer_with_evidence(self, query: str, top_k: int = 5) -> QaAnswer:
        hits = self._search_hits(query=query, top_k=top_k)
        answer = self._answer_with_llm(query=query, hits=hits)
        if not answer:
            if hits:
                answer = f"找到 {len(hits)} 条相关邮件，优先查看前两条证据。"
            else:
                answer = "未找到相关邮件证据。"
        evidence = [hit.snippet for hit in hits[:3]]
        return QaAnswer(answer=answer, hits=hits, evidence=evidence)
