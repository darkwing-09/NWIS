from typing import List


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
    """
    Splits text into chunks of maximum `chunk_size` words/tokens with `overlap` words.
    Respects word boundaries and preserves overlap between adjacent chunks.
    """
    if not text or not text.strip():
        return []

    words = text.split()
    if not words:
        return []

    if len(words) <= chunk_size:
        return [" ".join(words)]

    chunks: List[str] = []
    step = chunk_size - overlap
    if step <= 0:
        step = chunk_size

    for start in range(0, len(words), step):
        chunk_words = words[start : start + chunk_size]
        if not chunk_words:
            break
        chunks.append(" ".join(chunk_words))
        if start + chunk_size >= len(words):
            break

    return chunks
