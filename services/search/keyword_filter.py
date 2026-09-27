import re
from typing import List

from database.models.documents import DocumentChunk


def apply_keyword_filter(
    chunks: List[DocumentChunk],
    query_text: str,
) -> List[DocumentChunk]:
    """
    Applies lexical token matching as a pre-filter before vector ranking.
    Narrows candidate chunks to those containing relevant query keywords.
    """
    if not query_text or not query_text.strip() or not chunks:
        return []

    # Extract alphanumeric query terms of length >= 3
    terms = set(re.findall(r"\b[A-Za-z0-9_-]{3,}\b", query_text.lower()))
    if not terms:
        return chunks

    filtered: List[DocumentChunk] = []
    for chunk in chunks:
        chunk_lower = chunk.chunk_text.lower()
        if any(term in chunk_lower for term in terms):
            filtered.append(chunk)

    return filtered
