from fastapi import FastAPI
from pydantic import BaseModel

from engine import SearchEngine


app = FastAPI(
    title="MetaScale Search API",
    description="Member 10 - Search and Indexing Service",
    version="1.0.0",
)

search_engine = SearchEngine()


class Document(BaseModel):
    id: str
    text: str
    type: str = "post"


# Sample data for testing
# Sample data for testing
search_engine.add_document(
    "1",
    "Python database sharding and distributed systems",
    {
        "id": "1",
        "type": "post",
        "text": "Python database sharding and distributed systems",
    },
)

search_engine.add_document(
    "2",
    "Distributed database search indexing with Python",
    {
        "id": "2",
        "type": "post",
        "text": "Distributed database search indexing with Python",
    },
)

search_engine.add_document(
    "3",
    "MetaScale search engine",
    {
        "id": "3",
        "type": "user",
        "text": "MetaScale search engine",
    },
)

search_engine.add_document(
    "4",
    "#database #sharding #distributed",
    {
        "id": "4",
        "type": "hashtag",
        "text": "#database #sharding #distributed",
    },
)


@app.get("/")
def home():
    return {
        "service": "MetaScale Search API",
        "member": 10,
        "status": "running",
    }


@app.post("/index")
def index_document(document: Document):
    search_engine.add_document(
        document.id,
        document.text,
        document.model_dump(),
    )

    return {
        "message": "Document indexed successfully",
        "document_id": document.id,
    }


@app.get("/search")
def search(q: str):
    return {
        "query": q,
        "results": search_engine.search(q),
    }


@app.get("/autocomplete")
def autocomplete(prefix: str):
    return {
        "prefix": prefix,
        "suggestions": search_engine.autocomplete(prefix),
    }