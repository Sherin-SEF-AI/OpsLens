"""Knowledge Base / RAG Pipeline for OpsLens.

Features:
- Index all past postmortems, runbooks, and Notion pages
- Semantic search via embeddings for "we saw this exact issue before"
- Triage agent uses RAG to find similar past incidents
- Continuously learns from resolved incidents
- Stores embeddings locally with FAISS or in-memory fallback
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

from src.incidents.models import Incident

logger = structlog.get_logger()

# Embedding dimensions for different providers
EMBEDDING_DIMS = {
    "gemini": 768,
    "openai": 1536,
    "local": 384,
}

KB_DATA_DIR = Path(__file__).parent.parent.parent / "data" / "knowledge_base"


class KnowledgeDocument:
    """A document in the knowledge base."""

    def __init__(
        self,
        doc_id: str,
        title: str,
        content: str,
        doc_type: str,  # "incident", "postmortem", "runbook", "comment"
        source_id: str = "",
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ):
        self.doc_id = doc_id
        self.title = title
        self.content = content
        self.doc_type = doc_type
        self.source_id = source_id
        self.metadata = metadata or {}
        self.embedding = embedding
        self.created_at = datetime.now(timezone.utc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "title": self.title,
            "content": self.content[:5000],
            "doc_type": self.doc_type,
            "source_id": self.source_id,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
        }


class EmbeddingProvider:
    """Compute text embeddings using available provider."""

    def __init__(self, provider: str = "gemini", api_key: str = ""):
        self.provider = provider
        self.api_key = api_key
        self._enabled = bool(api_key)

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for a text string."""
        if not self._enabled:
            return self._simple_hash_embedding(text)

        if self.provider == "gemini":
            return await self._embed_gemini(text)
        elif self.provider == "openai":
            return await self._embed_openai(text)
        else:
            return self._simple_hash_embedding(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        results = []
        for text in texts:
            results.append(await self.embed(text))
        return results

    async def _embed_gemini(self, text: str) -> list[float]:
        """Use Gemini embedding model."""
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)

            # Truncate to model limit
            truncated = text[:8000]
            result = await client.aio.models.embed_content(
                model="models/text-embedding-004",
                contents=truncated,
            )
            return list(result.embeddings[0].values)
        except Exception as exc:
            logger.error("gemini_embedding_error", error=str(exc))
            return self._simple_hash_embedding(text)

    async def _embed_openai(self, text: str) -> list[float]:
        """Use OpenAI embedding model."""
        try:
            import httpx

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={
                        "model": "text-embedding-3-small",
                        "input": text[:8000],
                    },
                )
                resp.raise_for_status()
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as exc:
            logger.error("openai_embedding_error", error=str(exc))
            return self._simple_hash_embedding(text)

    @staticmethod
    def _simple_hash_embedding(text: str, dim: int = 384) -> list[float]:
        """Simple deterministic hash-based pseudo-embedding (fallback when no API).

        Not semantically meaningful but allows the system to function
        with basic keyword matching via cosine similarity of hash features.
        """
        import math

        # Tokenize into words
        words = text.lower().split()
        embedding = [0.0] * dim

        for i, word in enumerate(words):
            h = int(hashlib.md5(word.encode()).hexdigest(), 16)
            for j in range(min(8, dim)):
                idx = (h + j * 7) % dim
                embedding[idx] += 1.0 / (1 + i * 0.01)

        # Normalize
        magnitude = math.sqrt(sum(x * x for x in embedding))
        if magnitude > 0:
            embedding = [x / magnitude for x in embedding]

        return embedding


class KnowledgeBase:
    """In-memory knowledge base with semantic search.

    Uses cosine similarity for retrieval. Persists to disk as JSON.
    For production, replace with a vector database (Pinecone, Weaviate, etc.).
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider | None = None,
        data_dir: Path | None = None,
    ):
        self.embedder = embedding_provider or EmbeddingProvider()
        self.data_dir = data_dir or KB_DATA_DIR
        self._documents: dict[str, KnowledgeDocument] = {}
        self._embeddings: dict[str, list[float]] = {}
        self._loaded = False

    @property
    def document_count(self) -> int:
        return len(self._documents)

    def _ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)

    # --- Indexing ---

    async def index_incident(self, incident: Incident) -> str:
        """Index a resolved incident for future reference."""
        doc_id = f"incident-{incident.incident_id}"

        # Build rich text representation
        timeline_text = "\n".join(
            f"- {e.timestamp.strftime('%H:%M')} [{e.event_type.value}] {e.message}"
            for e in incident.timeline
        )

        content = (
            f"Incident: {incident.title}\n"
            f"Service: {incident.service}\n"
            f"Severity: {incident.severity}\n"
            f"Status: {incident.status.value}\n"
            f"Description: {incident.description}\n"
            f"Root Cause: {incident.root_cause}\n"
            f"Impact: {incident.impact}\n"
            f"Duration: {incident.duration_seconds or 0} seconds\n"
            f"\nTimeline:\n{timeline_text}"
        )

        doc = KnowledgeDocument(
            doc_id=doc_id,
            title=f"{incident.incident_id}: {incident.title}",
            content=content,
            doc_type="incident",
            source_id=incident.incident_id,
            metadata={
                "severity": incident.severity,
                "service": incident.service,
                "source": incident.source,
                "root_cause": incident.root_cause,
                "duration_seconds": incident.duration_seconds,
                "resolved": incident.status.value in ("Resolved", "Postmortem"),
            },
        )

        await self._add_document(doc)
        return doc_id

    async def index_postmortem(
        self,
        incident_id: str,
        title: str,
        content: str,
        page_id: str = "",
    ) -> str:
        """Index a postmortem document."""
        doc_id = f"postmortem-{incident_id}"

        doc = KnowledgeDocument(
            doc_id=doc_id,
            title=title,
            content=content,
            doc_type="postmortem",
            source_id=incident_id,
            metadata={"page_id": page_id},
        )

        await self._add_document(doc)
        return doc_id

    async def index_runbook(
        self,
        runbook_id: str,
        title: str,
        content: str,
        service: str = "",
        category: str = "",
    ) -> str:
        """Index a runbook for retrieval during incidents."""
        doc_id = f"runbook-{runbook_id}"

        doc = KnowledgeDocument(
            doc_id=doc_id,
            title=title,
            content=content,
            doc_type="runbook",
            source_id=runbook_id,
            metadata={"service": service, "category": category},
        )

        await self._add_document(doc)
        return doc_id

    async def index_text(
        self,
        doc_id: str,
        title: str,
        content: str,
        doc_type: str = "document",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Index arbitrary text content."""
        doc = KnowledgeDocument(
            doc_id=doc_id,
            title=title,
            content=content,
            doc_type=doc_type,
            metadata=metadata or {},
        )
        await self._add_document(doc)
        return doc_id

    async def _add_document(self, doc: KnowledgeDocument) -> None:
        """Add a document and compute its embedding."""
        # Compute embedding
        search_text = f"{doc.title}\n{doc.content[:4000]}"
        doc.embedding = await self.embedder.embed(search_text)

        self._documents[doc.doc_id] = doc
        self._embeddings[doc.doc_id] = doc.embedding

        # Persist
        self._save_to_disk()

        logger.info(
            "kb_document_indexed",
            doc_id=doc.doc_id,
            doc_type=doc.doc_type,
            content_length=len(doc.content),
        )

    # --- Semantic Search ---

    async def search(
        self,
        query: str,
        top_k: int = 5,
        doc_type: str = "",
        min_score: float = 0.0,
    ) -> list[dict[str, Any]]:
        """Search the knowledge base using semantic similarity.

        Args:
            query: Search query text
            top_k: Number of results to return
            doc_type: Filter by document type (incident, postmortem, runbook)
            min_score: Minimum similarity score (0.0 to 1.0)
        """
        if not self._documents:
            return []

        # Compute query embedding
        query_embedding = await self.embedder.embed(query)

        # Compute cosine similarity with all documents
        scores: list[tuple[str, float]] = []
        for doc_id, doc_embedding in self._embeddings.items():
            if doc_type and self._documents[doc_id].doc_type != doc_type:
                continue
            score = self._cosine_similarity(query_embedding, doc_embedding)
            if score >= min_score:
                scores.append((doc_id, score))

        # Sort by score descending
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for doc_id, score in scores[:top_k]:
            doc = self._documents[doc_id]
            results.append({
                "doc_id": doc.doc_id,
                "title": doc.title,
                "content_preview": doc.content[:500],
                "doc_type": doc.doc_type,
                "source_id": doc.source_id,
                "metadata": doc.metadata,
                "score": round(score, 4),
            })

        return results

    async def find_similar_incidents(
        self,
        incident: Incident,
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        """Find past incidents similar to the given one.

        Uses the incident's title, description, and service for the query.
        """
        query = (
            f"{incident.title} {incident.description} "
            f"service:{incident.service} severity:{incident.severity}"
        )
        return await self.search(
            query,
            top_k=top_k,
            doc_type="incident",
            min_score=0.1,
        )

    async def find_relevant_runbooks(
        self,
        service: str,
        symptoms: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Find runbooks relevant to the given service and symptoms."""
        query = f"runbook {service} {symptoms}"
        return await self.search(
            query,
            top_k=top_k,
            doc_type="runbook",
            min_score=0.1,
        )

    async def find_relevant_postmortems(
        self,
        query: str,
        top_k: int = 3,
    ) -> list[dict[str, Any]]:
        """Find postmortems relevant to a query."""
        return await self.search(
            query,
            top_k=top_k,
            doc_type="postmortem",
            min_score=0.1,
        )

    # --- Learning from Resolved Incidents ---

    async def learn_from_resolution(
        self,
        incident: Incident,
        resolution_notes: str = "",
    ) -> dict[str, Any]:
        """Index a resolved incident and its resolution for future learning.

        This should be called when an incident is resolved to capture
        the complete context including root cause and timeline.
        """
        doc_id = await self.index_incident(incident)

        # Also index key learnings as a separate document
        if incident.root_cause or resolution_notes:
            learning_content = (
                f"Service: {incident.service}\n"
                f"Symptoms: {incident.title} - {incident.description[:500]}\n"
                f"Root Cause: {incident.root_cause}\n"
                f"Resolution: {resolution_notes}\n"
                f"Severity: {incident.severity}\n"
                f"Duration: {incident.duration_seconds or 0}s\n"
            )
            learning_id = await self.index_text(
                doc_id=f"learning-{incident.incident_id}",
                title=f"Learning: {incident.title}",
                content=learning_content,
                doc_type="learning",
                metadata={
                    "incident_id": incident.incident_id,
                    "service": incident.service,
                    "severity": incident.severity,
                },
            )

        return {
            "indexed": True,
            "doc_id": doc_id,
            "total_documents": self.document_count,
        }

    # --- Persistence ---

    def _save_to_disk(self) -> None:
        """Save knowledge base to disk."""
        self._ensure_data_dir()

        data = {
            "documents": {
                doc_id: doc.to_dict() for doc_id, doc in self._documents.items()
            },
            "embeddings": {
                doc_id: emb for doc_id, emb in self._embeddings.items()
            },
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        kb_file = self.data_dir / "knowledge_base.json"
        kb_file.write_text(json.dumps(data, default=str))

    def load_from_disk(self) -> bool:
        """Load knowledge base from disk."""
        kb_file = self.data_dir / "knowledge_base.json"
        if not kb_file.exists():
            return False

        try:
            data = json.loads(kb_file.read_text())

            for doc_id, doc_data in data.get("documents", {}).items():
                self._documents[doc_id] = KnowledgeDocument(
                    doc_id=doc_data["doc_id"],
                    title=doc_data["title"],
                    content=doc_data["content"],
                    doc_type=doc_data["doc_type"],
                    source_id=doc_data.get("source_id", ""),
                    metadata=doc_data.get("metadata", {}),
                )

            self._embeddings = data.get("embeddings", {})
            self._loaded = True

            logger.info(
                "kb_loaded_from_disk",
                documents=len(self._documents),
            )
            return True

        except Exception as exc:
            logger.error("kb_load_error", error=str(exc))
            return False

    # --- Stats ---

    def get_stats(self) -> dict[str, Any]:
        """Get knowledge base statistics."""
        type_counts: dict[str, int] = {}
        for doc in self._documents.values():
            type_counts[doc.doc_type] = type_counts.get(doc.doc_type, 0) + 1

        return {
            "total_documents": self.document_count,
            "by_type": type_counts,
            "has_embeddings": len(self._embeddings),
            "loaded_from_disk": self._loaded,
        }

    # --- Utility ---

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors."""
        import math

        if len(a) != len(b) or not a:
            return 0.0

        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))

        if mag_a == 0 or mag_b == 0:
            return 0.0

        return dot / (mag_a * mag_b)
