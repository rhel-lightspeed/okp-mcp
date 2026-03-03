import argparse
from collections import defaultdict
import json
import requests
from typing import List
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer
import torch

# ---- globals / config ----
SOLR_HOST="127.0.0.1:8080"
SOLR_COLLECTION="portal-rag"

# EMBEDDING_MODEL="ibm-granite/granite-embedding-125m-english"
EMBEDDING_MODEL="ibm-granite/granite-embedding-30m-english"

# Max tokens to send back, this is a maximum of 4 chunks 512 tokens each
TOKEN_BUDGET=2048

# what is the minimum amount of chunks a doc must have before we compute a window
# if docs total chunks are under this we just return the whole doc
MIN_CHUNK_WINDOW=4

# minimum distance chunks must be from each other in order to be included
MIN_CHUNK_GAP=4

# debug flag (toggled by -d/--debug at runtime)
DEBUG = False
def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


class Chunk(BaseModel):
    chunk_index: int
    chunk: str


class Document(BaseModel):
    doc_id: str
    title: str
    reference_url: str
    text: str
    score: float
    originalScore: float | None = None
    matched_chunk_index: int | None = None
    chunks: List[Chunk] | None = None

class QueryResponse(BaseModel):
    question: str
    docs: List[Document]


def expand_context_window(chunks: List[dict], match_index: int, token_budget: int = TOKEN_BUDGET) -> List[dict]:
    total_tokens = 0
    selected_chunks = []

    n = len(chunks)
    left = match_index
    right = match_index + 1

    # Always include the matched chunk first
    center_chunk = chunks[match_index]
    total_tokens += center_chunk['num_tokens']
    selected_chunks.append(center_chunk)

    while total_tokens < token_budget and (left > 0 or right < n):
        added = False

        if left > 0:
            next_chunk = chunks[left - 1]
            if total_tokens + next_chunk['num_tokens'] <= token_budget:
                selected_chunks.insert(0, next_chunk)
                total_tokens += next_chunk['num_tokens']
                left -= 1
                added = True

        if right < n:
            next_chunk = chunks[right]
            if total_tokens + next_chunk['num_tokens'] <= token_budget:
                selected_chunks.append(next_chunk)
                total_tokens += next_chunk['num_tokens']
                right += 1
                added = True

        if not added:
            break

    return sorted(selected_chunks, key=lambda c: c['chunk_index'])


def okp_rag_symantic_query(question: str) -> QueryResponse:
    # Init model
    device = "cuda" if torch.cuda.is_available() else "cpu"

    dprint(f"encoding on device: {device}")

    model = SentenceTransformer(f"{EMBEDDING_MODEL}", device=device)

    # Generate vector embedding for question
    vector = model.encode([question])[0].tolist()
    knn_query = f"{{!knn f=chunk_vector topK=10}}{vector}"

    # Query chunk-level Solr collection
    solr_payload = {
        "params": {
            "q": knn_query,
            "rows": "5",
            "fl": "doc_id,parent_id,chunk_index,chunk,num_tokens,score"
        }
    }

    dprint(f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/semantic-search")

    chunk_res = requests.post(
        f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/semantic-search",
        headers={"Content-Type": "application/json"},
        params={"wt": "json"},
        json=solr_payload
    )
    chunk_res.raise_for_status()
    chunk_hits = chunk_res.json()["response"]["docs"]

    # Make sure chunk_hits are sorted by score desc (Solr usually does, but be explicit)
    chunk_hits.sort(key=lambda d: d.get("score", 0), reverse=True)

    dprint("<<<<< CHUNK HITS >>>>>")
    # Pretty print
    dprint(json.dumps(chunk_hits, indent=2))
    dprint("<<<<< END CHUNK HITS >>>>>>")

    docs = []

    # initialize a previous parent id and matched chunk index tracker
    kept_indices_by_parent = defaultdict(list)  # parent_id -> [kept chunk_index anchors]

    for match in chunk_hits:
        parent_id = match["parent_id"]
        matched_chunk_index = match["chunk_index"]

        # Skip if too close to ANY already-kept anchor in this parent
        if any(abs(matched_chunk_index - kept) < MIN_CHUNK_GAP for kept in kept_indices_by_parent[parent_id]):
            continue

        # Keep this anchor
        kept_indices_by_parent[parent_id].append(matched_chunk_index)

        # Fetch parent metadata
        parent_res = requests.get(
            f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/select",
            params={
                "q": f"id:\"{parent_id}\"",
                "fl": "doc_id,title,end_chunk_index,total_chunks,total_tokens,reference_url",
                "wt": "json"
            }
        )
        parent_res.raise_for_status()
        parent_doc = parent_res.json()["response"]["docs"][0]

        # If short doc, return all chunks
        if parent_doc["total_chunks"] < MIN_CHUNK_WINDOW or parent_doc["total_tokens"] <= TOKEN_BUDGET:
            chunks_res = requests.get(
                f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/select",
                params={
                    "q": f"parent_id:\"{parent_id}\"",
                    "rows": "100",
                    "sort": "chunk_index asc",
                    "fl": "chunk_index,chunk,num_tokens",
                    "wt": "json"
                }
            )
            chunks_res.raise_for_status()
            all_chunks = chunks_res.json()["response"]["docs"]
            selected = all_chunks
        else:
            # Bounded window around match (±10)
            window_start = max(0, matched_chunk_index - 10)
            if parent_doc["total_chunks"] > 0:
                window_end = min(parent_doc["total_chunks"] - 1, matched_chunk_index + 10)
            else:
                window_end = 0

            chunks_res = requests.get(
                f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/select",
                params={
                    "q": f"parent_id:\"{parent_id}\" AND chunk_index:[{window_start} TO {window_end}]",
                    "rows": "21",
                    "sort": "chunk_index asc",
                    "fl": "chunk_index,chunk,num_tokens",
                    "wt": "json"
                }
            )
            chunks_res.raise_for_status()
            context_chunks = chunks_res.json()["response"]["docs"]

            # Find local match index in response
            match_pos = next(i for i, c in enumerate(context_chunks) if c["chunk_index"] == matched_chunk_index)
            selected = expand_context_window(context_chunks, match_pos, token_budget=TOKEN_BUDGET)

        # Assemble the final Document
        text = "\n\n".join(c["chunk"] for c in selected)
        chunk_models = [Chunk(chunk_index=c["chunk_index"], chunk=c["chunk"]) for c in selected]

        doc_kwargs = {
            "doc_id": parent_doc["doc_id"],
            "title": parent_doc["title"],
            "reference_url": parent_doc.get("reference_url", f"/docs{parent_doc['doc_id']}"),
            "text": text,
            "score": match["score"]
        }

        # Only include these if debug mode is enabled
        if DEBUG:
            doc_kwargs.update({
                "matched_chunk_index": matched_chunk_index,
                "chunks": chunk_models,
            })

        doc = Document(**doc_kwargs)
        docs.append(doc)

    return QueryResponse(question=question, docs=docs)

def okp_rag_hybrid_query(question: str) -> QueryResponse:
    """
    Perform OKP Hybrid RAG query using Solr's built-in rerank functionality.
    """
    # Initialize the embedding model
    device = "cuda" if torch.cuda.is_available() else "cpu"

    dprint(f"encoding on device: {device}")

    model = SentenceTransformer(f"{EMBEDDING_MODEL}", device=device)

    # Generate vector embedding for question
    vector = model.encode([question])[0].tolist()
    
    # Create KNN query for reranking
    # knn_query = f"{{!knn f=chunk_vector topK=50}}{vector}"  # this gives good results
    knn_query = f"{{!vectorSimilarity f=chunk_vector minReturn=0.7}}{vector}"
    
    # HYBRID SEARCH: Use keyword search with KNN reranking
    # This uses Solr's built-in rerank functionality
    solr_payload = {
        "params": {
            "q": question,  # Main keyword query
            "rq": "{!rerank reRankQuery=$rqq reRankDocs=50 reRankWeight=5 reRankOperator=multiply}",  # Rerank with KNN
            "rqq": knn_query,  # KNN similarity query for reranking
            "rows": "5",
            "fl": "doc_id,parent_id,chunk_index,chunk,num_tokens,score,originalScore()",
            "fq": "is_chunk:true"
        }
    }
    
  
    # Query chunk-level Solr collection using hybrid-search endpoint
    chunk_res = requests.post(
        f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/hybrid-search",
        headers={"Content-Type": "application/json"},
        params={"wt": "json"},
        json=solr_payload
    )
    chunk_res.raise_for_status()
    chunk_hits = chunk_res.json()["response"]["docs"]

    # Make sure chunk_hits are sorted by score desc (reranked by semantic similarity)
    chunk_hits.sort(key=lambda d: d.get("score", 0), reverse=True)

    dprint("<<<<< CHUNK HITS >>>>>")
    dprint(json.dumps(chunk_hits, indent=2))
    dprint("<<<<< END CHUNK HITS >>>>>>")
    
    docs = []

    # Initialize a previous parent id and matched chunk index tracker
    kept_indices_by_parent = defaultdict(list)  # parent_id -> [kept chunk_index anchors]

    for match in chunk_hits:
        parent_id = match["parent_id"]
        matched_chunk_index = match["chunk_index"]

        # Skip if too close to ANY already-kept anchor in this parent
        if any(abs(matched_chunk_index - kept) < MIN_CHUNK_GAP for kept in kept_indices_by_parent[parent_id]):
            continue

        # Keep this anchor
        kept_indices_by_parent[parent_id].append(matched_chunk_index)

        # Fetch parent metadata
        parent_res = requests.get(
            f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/select",
            params={
                "q": f"id:\"{parent_id}\"",
                "fl": "doc_id,title,end_chunk_index,total_chunks,total_tokens,reference_url",
                "wt": "json"
            }
        )
        parent_res.raise_for_status()
        parent_doc = parent_res.json()["response"]["docs"][0]

        # If short doc, return all chunks
        if parent_doc["total_chunks"] < MIN_CHUNK_WINDOW or parent_doc["total_tokens"] <= TOKEN_BUDGET:
            chunks_res = requests.get(
                f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/select",
                params={
                    "q": f"parent_id:\"{parent_id}\"",
                    "rows": "100",
                    "sort": "chunk_index asc",
                    "fl": "chunk_index,chunk,num_tokens",
                    "wt": "json"
                }
            )
            chunks_res.raise_for_status()
            all_chunks = chunks_res.json()["response"]["docs"]
            selected = all_chunks
        else:
            # Bounded window around match (±10)
            window_start = max(0, matched_chunk_index - 10)
            if parent_doc["total_chunks"] > 0:
                window_end = min(parent_doc["total_chunks"] - 1, matched_chunk_index + 10)
            else:
                window_end = 0

            chunks_res = requests.get(
                f"http://{SOLR_HOST}/solr/{SOLR_COLLECTION}/select",
                params={
                    "q": f"parent_id:\"{parent_id}\" AND chunk_index:[{window_start} TO {window_end}]",
                    "rows": "21",
                    "sort": "chunk_index asc",
                    "fl": "chunk_index,chunk,num_tokens",
                    "wt": "json"
                }
            )
            chunks_res.raise_for_status()
            context_chunks = chunks_res.json()["response"]["docs"]

            # Find local match index in response
            match_pos = next(i for i, c in enumerate(context_chunks) if c["chunk_index"] == matched_chunk_index)
            selected = expand_context_window(context_chunks, match_pos, token_budget=TOKEN_BUDGET)

        # Assemble the final Document
        text = "\n\n".join(c["chunk"] for c in selected)
        chunk_models = [Chunk(chunk_index=c["chunk_index"], chunk=c["chunk"]) for c in selected]

        doc_kwargs = {
            "doc_id": parent_doc["doc_id"],
            "title": parent_doc["title"],
            "reference_url": parent_doc.get("reference_url", f"/docs{parent_doc['doc_id']}"),
            "text": text,
            "score": match["score"],
            "originalScore": match["originalScore()"]
        }

        # Only include these if debug mode is enabled
        if DEBUG:
            doc_kwargs.update({
                "matched_chunk_index": matched_chunk_index,
                "chunks": chunk_models,
            })

        doc = Document(**doc_kwargs)
        docs.append(doc)

    return QueryResponse(question=question, docs=docs)


def main():
    parser = argparse.ArgumentParser(description="Run an okp_rag_query against Solr")
    parser.add_argument(
        "-d", "--debug",
        action="store_true",
        help="Enable debug logging (prints intermediate results)"
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("-s", "--semantic", action="store_true", help="Use semantic search")
    mode.add_argument("-y", "--hybrid", action="store_true", help="Use hybrid search (keyword + semantic rerank)")

    parser.add_argument(
        "query",
        type=str,
        help="The query string to search for"
    )
    args = parser.parse_args()

    if not args.semantic and not args.hybrid:
        parser.error("You must specify either -s (semantic) or -h (hybrid) mode.")

    # set global debug flag
    global DEBUG
    DEBUG = args.debug

    if args.semantic:
        response = okp_rag_symantic_query(args.query)
    else:
        response = okp_rag_hybrid_query(args.query)

    print(response.model_dump_json(indent=2, exclude_none=True))


if __name__ == "__main__":
    main()

