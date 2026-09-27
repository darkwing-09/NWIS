import math
from typing import List, Optional, Tuple
import uuid
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database.models.documents import Document, DocumentChunk


class DocumentChunkRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def bulk_insert(self, chunks: List[DocumentChunk]) -> List[DocumentChunk]:
        """Bulk inserts chunks into database."""
        for c in chunks:
            self.db.add(c)
        self.db.flush()
        return chunks

    def get_chunks_by_document(self, doc_id: uuid.UUID) -> List[DocumentChunk]:
        stmt = (
            select(DocumentChunk)
            .where(DocumentChunk.doc_id == doc_id)
            .options(selectinload(DocumentChunk.document))
        )
        return list(self.db.scalars(stmt).all())

    def search_similar_chunks(
        self,
        query_vector: List[float],
        candidate_doc_ids: Optional[List[uuid.UUID]] = None,
        limit: int = 10,
    ) -> List[Tuple[DocumentChunk, float]]:
        """
        Retrieves most similar chunks via vector cosine distance.
        Works across SQLite (in-memory dot product / cosine fallback) and PostgreSQL pgvector.
        """
        stmt = select(DocumentChunk).options(selectinload(DocumentChunk.document))
        if candidate_doc_ids is not None:
            if not candidate_doc_ids:
                return []
            stmt = stmt.where(DocumentChunk.doc_id.in_(candidate_doc_ids))

        chunks = list(self.db.scalars(stmt).all())
        scored: List[Tuple[DocumentChunk, float]] = []

        q_norm = math.sqrt(sum(x * x for x in query_vector)) or 1.0

        for chunk in chunks:
            emb = chunk.embedding
            if not emb:
                continue
            # Handle list / array of floats
            c_vec = list(emb)
            dot = sum(a * b for a, b in zip(query_vector, c_vec))
            c_norm = math.sqrt(sum(y * y for y in c_vec)) or 1.0
            similarity = dot / (q_norm * c_norm)
            scored.append((chunk, similarity))

        # Highest similarity first
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]
