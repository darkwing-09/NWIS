from typing import Optional
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database.session import get_db
from domain.models.auth import AuthenticatedUser
from services.auth.rbac import require_permission
from services.search.query_parser import parse_query
from services.search.rag import SearchResponse, run_search

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(..., description="Natural language drilling query or operational question")
    well_id: Optional[uuid.UUID] = Field(None, description="Active well ID for geospatial and contextual scoping")
    formation: Optional[str] = Field(None, description="Target formation")


@router.post("", response_model=SearchResponse)
def search_rag(
    req: SearchRequest,
    user: AuthenticatedUser = Depends(require_permission("wells:read")),
    db: Session = Depends(get_db),
) -> SearchResponse:
    """
    RAG Search endpoint: performs hybrid vector retrieval and synthesized, citation-validated answers.
    """
    parsed_query = parse_query(
        raw_query=req.query,
        well_id=req.well_id,
        formation=req.formation,
        db=db,
    )
    response = run_search(query=parsed_query, db=db)
    return response
