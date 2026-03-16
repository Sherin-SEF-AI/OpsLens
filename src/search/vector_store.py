"""pgvector-based semantic search for incidents, postmortems, and runbooks."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single semantic search result."""

    content_type: str
    content_id: str
    content_text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

CREATE_EXTENSION_SQL = "CREATE EXTENSION IF NOT EXISTS vector;"

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_type VARCHAR(50) NOT NULL,
    content_id VARCHAR(255) NOT NULL,
    content_text TEXT NOT NULL,
    embedding vector(768),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS ix_embeddings_content
    ON embeddings (content_type, content_id);
"""

# IVFFlat index for approximate nearest neighbor search.
# We use lists=100 which is reasonable for up to ~100k rows.
# For larger datasets, increase lists accordingly.
CREATE_VECTOR_INDEX_SQL = """
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_indexes WHERE indexname = 'ix_embeddings_vector'
    ) THEN
        -- Only create the IVFFlat index if there are enough rows
        IF (SELECT count(*) FROM embeddings) >= 100 THEN
            CREATE INDEX ix_embeddings_vector
                ON embeddings USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100);
        END IF;
    END IF;
END $$;
"""

UPSERT_SQL = """
INSERT INTO embeddings (id, content_type, content_id, content_text, embedding, metadata, created_at)
VALUES (:id, :content_type, :content_id, :content_text, :embedding, :metadata, :created_at)
ON CONFLICT (id) DO UPDATE
    SET content_text = EXCLUDED.content_text,
        embedding = EXCLUDED.embedding,
        metadata = EXCLUDED.metadata,
        created_at = EXCLUDED.created_at;
"""

SEARCH_SQL = """
SELECT
    content_type,
    content_id,
    content_text,
    1 - (embedding <=> :query_embedding::vector) AS score,
    metadata
FROM embeddings
WHERE 1 - (embedding <=> :query_embedding::vector) >= :threshold
{type_filter}
ORDER BY embedding <=> :query_embedding::vector
LIMIT :limit;
"""

DELETE_SQL = """
DELETE FROM embeddings
WHERE content_type = :content_type AND content_id = :content_id;
"""

DELETE_ALL_SQL = "DELETE FROM embeddings WHERE content_type = :content_type;"


# ---------------------------------------------------------------------------
# Embedding providers
# ---------------------------------------------------------------------------

async def _get_gemini_embedding(text_input: str, api_key: str) -> list[float]:
    """Get text embedding using Google Gemini embedding model.

    Uses the text-embedding-004 model which produces 768-dimensional vectors.

    Args:
        text_input: Text to embed.
        api_key: Google AI API key.

    Returns:
        768-dimensional embedding vector.
    """
    from google import genai

    client = genai.Client(api_key=api_key)
    response = await client.aio.models.embed_content(
        model="text-embedding-004",
        contents=text_input[:8000],  # Model max context
    )
    # The response contains embeddings for each content item
    if response.embeddings:
        return list(response.embeddings[0].values)
    raise ValueError("No embedding returned from Gemini")


async def _get_openai_embedding(text_input: str, api_key: str) -> list[float]:
    """Get text embedding using OpenAI embeddings API.

    Uses text-embedding-3-small which can be configured to 768 dims.

    Args:
        text_input: Text to embed.
        api_key: OpenAI API key.

    Returns:
        768-dimensional embedding vector.
    """
    import httpx

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.openai.com/v1/embeddings",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "text-embedding-3-small",
                "input": text_input[:8000],
                "dimensions": 768,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return data["data"][0]["embedding"]


# ---------------------------------------------------------------------------
# VectorStore
# ---------------------------------------------------------------------------

class VectorStore:
    """pgvector-based semantic search engine for OpsLens content.

    Indexes incidents, postmortems, and runbooks as vector embeddings
    and provides similarity search across all indexed content.

    Usage::

        async with AsyncSessionLocal() as session:
            store = VectorStore(session)
            await store.initialize()
            await store.index_incident("OPSLENS-0001", "CPU spike", "High CPU on api-gateway", {})
            results = await store.search("cpu issues on gateway", limit=5)
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._initialized: bool = False

    # ------------------------------------------------------------------
    # Initialization
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the pgvector extension, embeddings table, and indexes.

        Safe to call multiple times (idempotent).
        """
        if self._initialized:
            return

        try:
            await self._session.execute(text(CREATE_EXTENSION_SQL))
            await self._session.execute(text(CREATE_TABLE_SQL))
            await self._session.execute(text(CREATE_INDEX_SQL))
            await self._session.flush()

            # Try to create vector index (needs enough rows)
            try:
                await self._session.execute(text(CREATE_VECTOR_INDEX_SQL))
                await self._session.flush()
            except Exception:
                logger.debug("vector_store.ivfflat_index_deferred",
                             reason="Not enough rows yet")

            self._initialized = True
            logger.info("vector_store.initialized")
        except Exception:
            logger.exception("vector_store.init_error")
            raise

    # ------------------------------------------------------------------
    # Embedding generation
    # ------------------------------------------------------------------

    async def get_embedding(self, text_input: str) -> list[float]:
        """Generate a 768-dimensional embedding for the given text.

        Uses the configured LLM provider (Gemini primary, OpenAI fallback).

        Args:
            text_input: Text to embed.

        Returns:
            768-dimensional float vector.

        Raises:
            ValueError: If no embedding provider is configured.
        """
        from src.config import get_config
        config = get_config()

        # Try Gemini first (primary provider for OpsLens)
        if config.GEMINI_API_KEY:
            try:
                return await _get_gemini_embedding(text_input, config.GEMINI_API_KEY)
            except Exception as exc:
                logger.warning(
                    "vector_store.gemini_embedding_failed",
                    error=str(exc),
                )

        # Fallback to OpenAI if available
        openai_key = os.environ.get("OPENAI_API_KEY", "")
        if openai_key:
            try:
                return await _get_openai_embedding(text_input, openai_key)
            except Exception as exc:
                logger.warning(
                    "vector_store.openai_embedding_failed",
                    error=str(exc),
                )

        # If Anthropic is configured but no embedding model, raise
        raise ValueError(
            "No embedding provider available. "
            "Configure GEMINI_API_KEY or OPENAI_API_KEY."
        )

    # ------------------------------------------------------------------
    # CRUD operations
    # ------------------------------------------------------------------

    async def store_embedding(
        self,
        content_type: str,
        content_id: str,
        text_content: str,
        embedding: list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store or update an embedding in the database.

        Args:
            content_type: Type of content (incident, postmortem, runbook).
            content_id: Unique identifier for the content.
            text_content: Original text that was embedded.
            embedding: 768-dimensional float vector.
            metadata: Optional JSON metadata.
        """
        import json as _json

        embedding_id = uuid.uuid5(
            uuid.NAMESPACE_DNS, f"{content_type}:{content_id}"
        )
        embedding_str = str(embedding)
        metadata_json = _json.dumps(metadata or {}, default=str)

        await self._session.execute(
            text(UPSERT_SQL),
            {
                "id": embedding_id,
                "content_type": content_type,
                "content_id": content_id,
                "content_text": text_content[:10000],
                "embedding": embedding_str,
                "metadata": metadata_json,
                "created_at": datetime.now(timezone.utc),
            },
        )
        await self._session.flush()
        logger.debug(
            "vector_store.stored",
            content_type=content_type,
            content_id=content_id,
        )

    async def search_similar(
        self,
        query_embedding: list[float],
        content_type: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[SearchResult]:
        """Search for content similar to the query embedding.

        Args:
            query_embedding: 768-dimensional query vector.
            content_type: Optional filter by content type.
            limit: Maximum results to return.
            threshold: Minimum cosine similarity score (0.0 to 1.0).

        Returns:
            List of SearchResult ordered by descending similarity.
        """
        type_filter = ""
        if content_type:
            type_filter = f"AND content_type = '{content_type}'"

        query = SEARCH_SQL.format(type_filter=type_filter)
        embedding_str = str(query_embedding)

        result = await self._session.execute(
            text(query),
            {
                "query_embedding": embedding_str,
                "threshold": threshold,
                "limit": limit,
            },
        )

        results: list[SearchResult] = []
        for row in result.fetchall():
            import json as _json
            meta = row[4]
            if isinstance(meta, str):
                try:
                    meta = _json.loads(meta)
                except (ValueError, TypeError):
                    meta = {}
            elif meta is None:
                meta = {}

            results.append(SearchResult(
                content_type=row[0],
                content_id=row[1],
                content_text=row[2],
                score=float(row[3]),
                metadata=meta,
            ))

        logger.debug(
            "vector_store.search_completed",
            results_count=len(results),
            content_type=content_type,
            threshold=threshold,
        )
        return results

    async def delete_embeddings(
        self, content_type: str, content_id: str
    ) -> None:
        """Delete all embeddings for a specific content item.

        Args:
            content_type: Type of content.
            content_id: Unique identifier.
        """
        await self._session.execute(
            text(DELETE_SQL),
            {"content_type": content_type, "content_id": content_id},
        )
        await self._session.flush()
        logger.debug(
            "vector_store.deleted",
            content_type=content_type,
            content_id=content_id,
        )

    # ------------------------------------------------------------------
    # High-level indexing methods
    # ------------------------------------------------------------------

    async def index_incident(
        self,
        incident_id: str,
        title: str,
        description: str,
        agent_analyses: dict[str, Any] | None = None,
    ) -> None:
        """Index an incident for semantic search.

        Combines the title, description, and agent analyses into a single
        searchable text block.

        Args:
            incident_id: Human-readable incident ID.
            title: Incident title.
            description: Incident description.
            agent_analyses: Optional dict of agent analysis results.
        """
        # Build comprehensive text for embedding
        parts = [
            f"Incident: {title}",
            f"Description: {description}" if description else "",
        ]

        if agent_analyses:
            for agent_name, analysis in agent_analyses.items():
                if isinstance(analysis, dict):
                    text_content = analysis.get("text", analysis.get("analysis", ""))
                else:
                    text_content = str(analysis)
                if text_content:
                    parts.append(f"{agent_name}: {str(text_content)[:1000]}")

        combined_text = "\n".join(p for p in parts if p)

        try:
            embedding = await self.get_embedding(combined_text)
            await self.store_embedding(
                content_type="incident",
                content_id=incident_id,
                text_content=combined_text,
                embedding=embedding,
                metadata={
                    "title": title,
                    "incident_id": incident_id,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "vector_store.incident_indexed",
                incident_id=incident_id,
            )
        except Exception:
            logger.exception(
                "vector_store.incident_index_error",
                incident_id=incident_id,
            )

    async def index_postmortem(
        self,
        postmortem_id: str,
        content: str,
    ) -> None:
        """Index a postmortem document for semantic search.

        Args:
            postmortem_id: Postmortem identifier.
            content: Full postmortem text content.
        """
        try:
            embedding = await self.get_embedding(content[:8000])
            await self.store_embedding(
                content_type="postmortem",
                content_id=postmortem_id,
                text_content=content[:10000],
                embedding=embedding,
                metadata={
                    "postmortem_id": postmortem_id,
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "vector_store.postmortem_indexed",
                postmortem_id=postmortem_id,
            )
        except Exception:
            logger.exception(
                "vector_store.postmortem_index_error",
                postmortem_id=postmortem_id,
            )

    async def index_runbook(
        self,
        runbook_id: str,
        name: str,
        steps: list[dict[str, Any]],
    ) -> None:
        """Index a runbook for semantic search.

        Args:
            runbook_id: Runbook identifier.
            name: Runbook name.
            steps: List of step dicts with name, type, and config.
        """
        # Build text from runbook content
        parts = [f"Runbook: {name}"]
        for i, step in enumerate(steps):
            step_name = step.get("name", f"Step {i + 1}")
            step_type = step.get("type", "unknown")
            step_desc = step.get("config", {}).get("description", "")
            step_cmd = step.get("config", {}).get("command", "")
            line = f"Step {i + 1}: [{step_type}] {step_name}"
            if step_desc:
                line += f" - {step_desc}"
            if step_cmd:
                line += f" (cmd: {step_cmd})"
            parts.append(line)

        combined_text = "\n".join(parts)

        try:
            embedding = await self.get_embedding(combined_text)
            await self.store_embedding(
                content_type="runbook",
                content_id=runbook_id,
                text_content=combined_text,
                embedding=embedding,
                metadata={
                    "runbook_id": runbook_id,
                    "name": name,
                    "steps_count": len(steps),
                    "indexed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            logger.info(
                "vector_store.runbook_indexed",
                runbook_id=runbook_id,
                name=name,
            )
        except Exception:
            logger.exception(
                "vector_store.runbook_index_error",
                runbook_id=runbook_id,
            )

    # ------------------------------------------------------------------
    # Search convenience methods
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        content_type: str | None = None,
        limit: int = 10,
        threshold: float = 0.7,
    ) -> list[SearchResult]:
        """Search for similar content using a natural language query.

        This is the main search entry point. It generates an embedding
        for the query text and performs vector similarity search.

        Args:
            query: Natural language search query.
            content_type: Optional filter (incident, postmortem, runbook).
            limit: Maximum results.
            threshold: Minimum similarity score.

        Returns:
            List of SearchResult ordered by relevance.
        """
        query_embedding = await self.get_embedding(query)
        return await self.search_similar(
            query_embedding,
            content_type=content_type,
            limit=limit,
            threshold=threshold,
        )

    async def find_similar_incidents(
        self,
        title: str,
        description: str = "",
        limit: int = 5,
        threshold: float = 0.6,
    ) -> list[SearchResult]:
        """Find incidents similar to a given title and description.

        Useful for deduplication and correlation during triage.

        Args:
            title: Incident title.
            description: Incident description.
            limit: Maximum results.
            threshold: Minimum similarity score.

        Returns:
            List of similar incident SearchResults.
        """
        query_text = f"{title}\n{description}" if description else title
        return await self.search(
            query_text,
            content_type="incident",
            limit=limit,
            threshold=threshold,
        )

    async def find_relevant_runbooks(
        self,
        incident_title: str,
        incident_description: str = "",
        limit: int = 3,
        threshold: float = 0.5,
    ) -> list[SearchResult]:
        """Find runbooks relevant to a given incident.

        Args:
            incident_title: Incident title.
            incident_description: Incident description.
            limit: Maximum results.
            threshold: Minimum similarity score.

        Returns:
            List of relevant runbook SearchResults.
        """
        query_text = f"{incident_title}\n{incident_description}" if incident_description else incident_title
        return await self.search(
            query_text,
            content_type="runbook",
            limit=limit,
            threshold=threshold,
        )

    # ------------------------------------------------------------------
    # Reindexing
    # ------------------------------------------------------------------

    async def reindex_all(self) -> dict[str, int]:
        """Perform a full reindex of all content from the database.

        Reads incidents, postmortems (from agent results), and runbook
        executions from the DB and re-generates all embeddings.

        Returns:
            Dict with counts of indexed items per content type.
        """
        from src.database.models import AgentResult, AgentTypeEnum, Incident, RunbookExecution

        logger.info("vector_store.reindex_started")
        counts: dict[str, int] = {"incident": 0, "postmortem": 0, "runbook": 0}

        # Clear existing embeddings
        for ct in ("incident", "postmortem", "runbook"):
            await self._session.execute(
                text(DELETE_ALL_SQL), {"content_type": ct}
            )
        await self._session.flush()

        # Reindex incidents
        from sqlalchemy import select
        incidents_result = await self._session.execute(select(Incident))
        incidents = incidents_result.scalars().all()

        for incident in incidents:
            # Get agent analyses for this incident
            agent_stmt = select(AgentResult).where(
                AgentResult.incident_id == incident.id
            )
            agent_result = await self._session.execute(agent_stmt)
            agent_rows = agent_result.scalars().all()

            analyses: dict[str, Any] = {}
            for ar in agent_rows:
                analyses[ar.agent_type.value] = {"text": ar.analysis}

                # If this is a postmortem, also index it separately
                if ar.agent_type == AgentTypeEnum.POSTMORTEM and ar.analysis:
                    pm_id = f"pm-{incident.incident_id}"
                    try:
                        await self.index_postmortem(pm_id, ar.analysis)
                        counts["postmortem"] += 1
                    except Exception:
                        logger.exception(
                            "vector_store.reindex_postmortem_error",
                            incident_id=incident.incident_id,
                        )

            try:
                await self.index_incident(
                    incident.incident_id,
                    incident.title,
                    incident.description or "",
                    analyses,
                )
                counts["incident"] += 1
            except Exception:
                logger.exception(
                    "vector_store.reindex_incident_error",
                    incident_id=incident.incident_id,
                )

        # Reindex runbook executions
        exec_result = await self._session.execute(select(RunbookExecution))
        executions = exec_result.scalars().all()

        seen_runbooks: set[str] = set()
        for execution in executions:
            rb_name = execution.runbook_name
            if rb_name in seen_runbooks:
                continue
            seen_runbooks.add(rb_name)

            output = execution.output or {}
            steps_data = output.get("steps", [])
            rb_id = execution.runbook_notion_id or f"rb-{rb_name}"

            try:
                await self.index_runbook(rb_id, rb_name, steps_data)
                counts["runbook"] += 1
            except Exception:
                logger.exception(
                    "vector_store.reindex_runbook_error",
                    runbook_name=rb_name,
                )

        await self._session.flush()
        logger.info("vector_store.reindex_completed", counts=counts)
        return counts
