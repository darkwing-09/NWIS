from typing import List, Optional
import uuid
from sqlalchemy.orm import Session
import structlog

from config.errors import EmbeddingProviderError
from database.models.documents import Document, DocumentChunk
from database.repositories.document_chunks import DocumentChunkRepository
from domain.ai.embedding_provider import EmbeddingProvider, get_embedding_provider

logger = structlog.get_logger("nwis.search.embeddings")


def embed_chunks(
    chunks: List[str],
    document_id: uuid.UUID,
    db: Session,
    provider: Optional[EmbeddingProvider] = None,
) -> List[DocumentChunk]:
    """
    Calls embedding provider per chunk and persists DocumentChunk records.
    On failure, marks document.extraction_status = 'embedding_failed' and raises EmbeddingProviderError.
    """
    if not chunks:
        return []

    p = provider or get_embedding_provider()

    try:
        vectors = p.embed(chunks)
    except Exception as e:
        logger.error("embedding_generation_failed", doc_id=str(document_id), error=str(e))
        doc = db.get(Document, document_id)
        if doc:
            doc.extraction_status = "embedding_failed"
            db.flush()
        raise EmbeddingProviderError(f"Embedding generation failed: {e}") from e

    repo = DocumentChunkRepository(db)
    chunk_entities: List[DocumentChunk] = []

    for text, vec in zip(chunks, vectors):
        chunk_obj = DocumentChunk(
            chunk_id=uuid.uuid4(),
            doc_id=document_id,
            chunk_text=text,
            embedding=vec,
        )
        chunk_entities.append(chunk_obj)

    repo.bulk_insert(chunk_entities)
    return chunk_entities
