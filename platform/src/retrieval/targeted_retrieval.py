"""Targeted re-retrieval module.

Called when the Answer Agent returns "insufficient" or "rewrite_search".
Fetches additional context focused on the missing entity or the rewritten query,
then merges it into the STM without duplicating already-seen entities.

Public API
----------
fetch(stm, agent_response, repo_id, db) -> list[ExpandedContext]
    The only function callers need.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from src.retrieval.models import ExpandedContext, RetrievalResult
from src.retrieval.vector_search import search
from src.retrieval.relationship_expander import expand

if TYPE_CHECKING:
    from src.generation.answer_agent import AgentResponse
    from src.pipeline.memory import ShortTermMemory

logger = logging.getLogger(__name__)

# How many entities to fetch in each targeted retrieval pass
_TARGETED_TOP_K = 5


def fetch(
    stm: "ShortTermMemory",
    agent_response: "AgentResponse",
    repo_id: str,
    db: Session,
) -> list[ExpandedContext]:
    """Fetch new context based on the Answer Agent's feedback.

    Strategy:
    - "insufficient": search for the missing entity name
    - "rewrite_search": search with the rewritten query

    New entities are filtered against stm.visited_entity_ids to avoid
    duplicating context already present in the STM.

    Parameters
    ----------
    stm:
        Current Short-Term Memory state.
    agent_response:
        The Answer Agent response that triggered re-retrieval.
    repo_id:
        Target repository ID.
    db:
        Active database session.

    Returns
    -------
    list[ExpandedContext]
        New expanded contexts NOT already present in the STM.
        The caller is responsible for merging these into stm.retrieved_chunks
        and updating stm.visited_entity_ids.
    """
    # Determine the search query for this targeted pass
    if agent_response.status == "rewrite_search" and agent_response.rewrite_query:
        targeted_query = agent_response.rewrite_query
        logger.debug(
            "Targeted retrieval (rewrite): %r — iteration=%d",
            targeted_query,
            stm.iteration_count,
        )
    elif agent_response.status == "insufficient" and agent_response.missing:
        missing_entity = agent_response.missing.get("entity", "")
        if missing_entity:
            targeted_query = missing_entity
        else:
            # Fall back to the original goal if no specific entity was identified
            targeted_query = stm.goal
        logger.debug(
            "Targeted retrieval (insufficient): %r — iteration=%d",
            targeted_query,
            stm.iteration_count,
        )
    else:
        logger.debug(
            "Targeted retrieval: no specific target, using original goal — iteration=%d",
            stm.iteration_count,
        )
        targeted_query = stm.goal

    # Vector search for the targeted query
    try:
        results: list[RetrievalResult] = search(
            query=targeted_query,
            repo_id=repo_id,
            top_k=_TARGETED_TOP_K,
            db_session=db,
        )
    except Exception as exc:
        logger.warning("Targeted retrieval: search failed: %s", exc)
        return []

    if not results:
        logger.debug("Targeted retrieval: no results for %r", targeted_query)
        return []

    # Filter out entity IDs already in the STM
    new_results = [r for r in results if r.entity_id not in stm.visited_entity_ids]
    if not new_results:
        logger.debug(
            "Targeted retrieval: all %d results already in STM — skipping expansion",
            len(results),
        )
        return []

    # Graph expansion on the new results
    try:
        new_expanded: list[ExpandedContext] = expand(
            retrieved_results=new_results,
            repo_id=repo_id,
            db_session=db,
        )
    except Exception as exc:
        logger.warning("Targeted retrieval: expansion failed: %s", exc)
        # Return search results without expansion rather than failing entirely
        new_expanded = [
            ExpandedContext(core=r) for r in new_results
        ]

    # Update STM visited set
    for r in new_results:
        stm.visited_entity_ids.add(r.entity_id)

    logger.debug(
        "Targeted retrieval: fetched %d new entities (query=%r, iteration=%d)",
        len(new_expanded),
        targeted_query,
        stm.iteration_count,
    )
    return new_expanded
