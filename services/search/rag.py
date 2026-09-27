import re
from typing import List, Optional, Set
import uuid
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
import structlog

from database.models.documents import Document, DocumentChunk
from database.models.events import Event
from database.repositories.document_chunks import DocumentChunkRepository
from domain.ai.embedding_provider import get_embedding_provider
from domain.ai.llm_provider import HeuristicLLMProvider, LLMProvider
from services.search.filters import exclude_superseded
from services.search.query_parser import ParsedQuery

logger = structlog.get_logger("nwis.search.rag")


class RankedChunk(BaseModel):
    chunk_id: uuid.UUID
    doc_id: uuid.UUID
    well_id: Optional[uuid.UUID] = None
    well_name: Optional[str] = None
    chunk_text: str
    similarity_score: float = 1.0
    duplicate_group_id: Optional[uuid.UUID] = None

    model_config = ConfigDict(from_attributes=True)


class RawSynthesisResult(BaseModel):
    answer: str
    raw_response: str


class SourceCitation(BaseModel):
    chunk_id: str
    doc_id: Optional[str] = None
    well_name: Optional[str] = None
    snippet: str


class SearchResponse(BaseModel):
    answer: str
    sources: List[SourceCitation] = Field(default_factory=list)
    confidence: Optional[float] = None


RAG_SYSTEM_PROMPT = (
    "You are NWIS Assistant for Oil India Limited drilling engineers. "
    "Synthesize an answer using ONLY the factual evidence in the context block. "
    "Every factual sentence MUST include an exact citation in the format [source: <chunk_id>]. "
    "Never cite sources not in the context. Never make ungrounded inferences."
)


def dedup_evidence(chunks: List[RankedChunk], db: Optional[Session] = None) -> List[RankedChunk]:
    """
    Groups chunks by duplicate_group_id where the chunk's document maps to duplicate events,
    keeping only the highest-ranked chunk per group.
    """
    seen_groups: Set[uuid.UUID] = set()
    deduped: List[RankedChunk] = []

    for c in chunks:
        # Check if chunk has duplicate_group_id
        gid = c.duplicate_group_id
        if gid:
            if gid in seen_groups:
                continue
            seen_groups.add(gid)
        deduped.append(c)

    return deduped


def construct_context(chunks: List[RankedChunk]) -> str:
    """
    Builds LLM prompt context block with clear boundary markers.
    Each chunk is tagged with [source: <chunk_id>].
    """
    if not chunks:
        return "NO_RELEVANT_CONTEXT_FOUND"

    blocks = []
    for c in chunks:
        header = f"--- BEGIN SOURCE: {c.chunk_id} (doc: {c.doc_id}) ---"
        footer = f"--- END SOURCE: {c.chunk_id} ---"
        blocks.append(f"{header}\n{c.chunk_text}\n{footer}")

    return "\n\n".join(blocks)


def synthesize_answer(
    query: ParsedQuery,
    context: str,
    llm: Optional[LLMProvider] = None,
    retry_reminder: bool = False,
) -> RawSynthesisResult:
    """
    Invokes LLM with RAG_SYSTEM_PROMPT and delimited context data.
    Enforces prompt injection boundaries per Part 19.
    """
    provider = llm or HeuristicLLMProvider()

    prompt = f"Active Well Context: {query.well_id or 'None'}, Target Formation: {query.formation or 'None'}\n"
    prompt += f"User Question: {query.text}\n\n"
    prompt += f"CONTEXT DATA:\n{context}\n\n"

    if retry_reminder:
        prompt += (
            "CRITICAL: Previous synthesis failed citation validation. "
            "Cite ONLY valid source IDs provided in the context data using [source: <chunk_id>]. "
            "Every single factual claim must carry a citation tag.\n"
        )

    ans = provider.generate_text(prompt=prompt, system_prompt=RAG_SYSTEM_PROMPT)
    return RawSynthesisResult(answer=ans, raw_response=ans)


def validate_citations(
    result: RawSynthesisResult,
    retrieved_chunk_ids: Set[str],
    chunks: Optional[List[RankedChunk]] = None,
) -> Optional[SearchResponse]:
    """
    Core safety validation gate:
    1. Parse cited source IDs from result.answer (expects [source: <id>]).
    2. Any cited ID not in retrieved_chunk_ids -> returns None (triggers reject & regenerate).
    3. Any sentence with factual claims lacking citation -> returns None.
    4. Returns SearchResponse if all citations are valid and grounded.
    """
    answer = result.answer.strip()
    cited_ids = set(re.findall(r"\[source:\s*([A-Za-z0-9_-]+)\]", answer, re.IGNORECASE))

    # Reject if no citations were produced at all
    if not cited_ids:
        logger.warning("citation_validation_failed_no_citations", answer_snippet=answer[:80])
        return None

    # Check for hallucinated / ungrounded citations
    normalized_retrieved = {str(cid).lower() for cid in retrieved_chunk_ids}
    for cid in cited_ids:
        if cid.lower() not in normalized_retrieved:
            logger.warning("citation_validation_failed_hallucinated_id", cited_id=cid)
            return None

    # Sentence-level check: factual sentences must carry citation
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", answer) if s.strip()]
    for s in sentences:
        # Ignore short introductory or conversational hedges
        if len(s) > 20 and not any(s.lower().startswith(h) for h in ("note:", "summary:", "no confident", "in summary")):
            if not re.search(r"\[source:\s*[A-Za-z0-9_-]+\]", s, re.IGNORECASE):
                logger.warning("citation_validation_failed_uncited_factual_sentence", sentence=s)
                return None

    # Build sources list
    chunk_map = {str(c.chunk_id).lower(): c for c in (chunks or [])}
    citations: List[SourceCitation] = []
    for cid in cited_ids:
        chunk_obj = chunk_map.get(cid.lower())
        snippet = chunk_obj.chunk_text[:150] if chunk_obj else "Offset well historical record"
        citations.append(
            SourceCitation(
                chunk_id=cid,
                doc_id=str(chunk_obj.doc_id) if chunk_obj else None,
                well_name=chunk_obj.well_name if chunk_obj else None,
                snippet=snippet,
            )
        )

    return SearchResponse(
        answer=answer,
        sources=citations,
        confidence=0.92,
    )


def run_search(
    query: ParsedQuery,
    db: Session,
    llm: Optional[LLMProvider] = None,
    limit: int = 10,
) -> SearchResponse:
    """
    Full Search + RAG pipeline entrypoint:
    1. Resolve candidate documents based on nearby wells and exclude superseded records.
    2. Embed query vector and execute vector similarity ranking.
    3. Apply duplicate evidence deduplication.
    4. Construct grounded context.
    5. Synthesize answer with LLM.
    6. Validate citations (with 1 automatic retry and fallback to safe refusal).
    """
    # 1. Candidate document filter
    candidate_doc_ids: Optional[List[uuid.UUID]] = None
    if query.nearby_well_ids is not None:
        well_scope = list(query.nearby_well_ids)
        if query.well_id and query.well_id not in well_scope:
            well_scope.append(query.well_id)
        stmt = select(Document.doc_id).where(Document.well_id.in_(well_scope))
        all_doc_ids = list(db.scalars(stmt).all())
        candidate_doc_ids = exclude_superseded(all_doc_ids, db=db)

    # 2. Vector similarity retrieval
    embed_provider = get_embedding_provider()
    query_vec = embed_provider.embed_one(query.text)

    chunk_repo = DocumentChunkRepository(db)
    similar = chunk_repo.search_similar_chunks(
        query_vector=query_vec,
        candidate_doc_ids=candidate_doc_ids,
        limit=limit,
    )

    if not similar:
        return SearchResponse(
            answer="No relevant nearby well records or historical documents found matching this query.",
            sources=[],
            confidence=0.0,
        )

    # Convert to RankedChunks
    ranked_chunks: List[RankedChunk] = []
    for chunk_entity, score in similar:
        well_id = chunk_entity.document.well_id if chunk_entity.document else None
        well_name = chunk_entity.document.well.name if (chunk_entity.document and chunk_entity.document.well) else None
        ranked_chunks.append(
            RankedChunk(
                chunk_id=chunk_entity.chunk_id,
                doc_id=chunk_entity.doc_id,
                well_id=well_id,
                well_name=well_name,
                chunk_text=chunk_entity.chunk_text,
                similarity_score=score,
            )
        )

    # 3. Deduplicate evidence
    deduped_chunks = dedup_evidence(ranked_chunks, db=db)
    retrieved_chunk_ids = {str(c.chunk_id) for c in deduped_chunks}

    # 4. Construct context
    context = construct_context(deduped_chunks)

    # 5. Synthesize answer
    first_res = synthesize_answer(query=query, context=context, llm=llm, retry_reminder=False)

    # 6. Validate citations
    validated = validate_citations(first_res, retrieved_chunk_ids, chunks=deduped_chunks)
    if validated:
        return validated

    # Retry once with explicit citation reminder
    logger.info("retrying_synthesis_with_citation_reminder", query=query.text[:50])
    retry_res = synthesize_answer(query=query, context=context, llm=llm, retry_reminder=True)
    retry_validated = validate_citations(retry_res, retrieved_chunk_ids, chunks=deduped_chunks)
    if retry_validated:
        return retry_validated

    # Safe refusal fallback: NEVER return an uncited, hallucinated, or mismatched answer
    logger.warning("citation_validation_failed_falling_back_to_safe_refusal", query=query.text[:50])
    return SearchResponse(
        answer="No confident, source-grounded answer found for this query.",
        sources=[],
        confidence=0.0,
    )
