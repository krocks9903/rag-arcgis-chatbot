import hashlib
import json
import os
import re
import shutil
import traceback
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain_community.document_loaders import CSVLoader
from langchain_community.vectorstores import FAISS
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough
from langchain_groq import ChatGroq
from langchain_huggingface import HuggingFaceEmbeddings
from pydantic import BaseModel, Field

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BACKEND_DIR, ".env"))

INDEX_DIR = os.path.join(BACKEND_DIR, "faiss_index")
HASH_FILE = os.path.join(INDEX_DIR, "csv_hash.json")
DATA_DIR = os.path.join(BACKEND_DIR, "data")
DEFAULT_CSV_PATH = os.path.join(DATA_DIR, "data.csv")

vectorstore: FAISS | None = None
retriever = None
llm: ChatGroq | None = None
embeddings: HuggingFaceEmbeddings | None = None
record_count = 0
_last_query: list[str] = [""]

PROMPT_TEMPLATE = """You are a helpful assistant for the Village of Estero's Engage Estero platform.
You help residents understand Planning, Zoning & Design Board decisions using official meeting records.

RULES — follow exactly:
1. Only use facts from the Context. Never invent any detail.
2. If no relevant info exists, set summary to exactly: "I don't have records on that." and projects to [].
3. Write plain English in summary and project summaries. No markdown, no asterisks.
4. For each matching project, fill every field from the Context only.
5. document_url must be copied exactly from Document_Link in the context — never invent URLs.
6. status must be one of: Approved, Denied, Continued, or No decision recorded.

Return ONLY valid JSON (no markdown fences) with this exact shape:
{{
  "summary": "one closing sentence",
  "projects": [
    {{
      "title": "short project name from ProjectName",
      "id": "ApplicationID",
      "location": "Location or LocationName",
      "summary": "1-2 sentences",
      "status": "Approved | Denied | Continued | No decision recorded",
      "date": "MeetingDate",
      "document_url": "Document_Link"
    }}
  ]
}}

Context:
{context}

Question: {question}

JSON:"""


class ProjectOut(BaseModel):
    title: str = ""
    id: str = ""
    location: str = ""
    summary: str = ""
    status: str = "No decision recorded"
    date: str = ""
    document_url: str = ""


class ChatResponse(BaseModel):
    summary: str
    projects: list[ProjectOut] = Field(default_factory=list)
    answer: str = ""  # legacy plain-text field for older clients


class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"


def enrich_doc(doc: Document) -> Document:
    """Build a keyword-rich search header for FAISS retrieval."""
    lines = doc.page_content.strip().split("\n")
    fields: dict[str, str] = {}
    for line in lines:
        if ": " in line:
            k, v = line.split(": ", 1)
            fields[k.strip()] = v.strip()

    raw_name = fields.get("ProjectName", "")
    location = fields.get("Location", "") or fields.get("LocationName", "")
    app_id = fields.get("ApplicationID", "")
    date = fields.get("MeetingDate", "")
    outcome = fields.get("Outcome", "")
    action = fields.get("ActionTaken", "")
    status = fields.get("Status", "")

    short_name = re.split(r"\s*\((?:DOS|DCI|LDO|ADD|CPA|REZ)\d{4}", raw_name)[0].strip()
    short_name = re.sub(r"\s*-\s*Development Order.*", "", short_name, flags=re.IGNORECASE).strip()
    short_name = short_name[:80]
    outcome_short = (outcome or action or status)[:60]

    header_parts = filter(None, [short_name, app_id, location, outcome_short, date])
    header = " | ".join(header_parts)

    return Document(
        page_content=f"SEARCH: {header}\n\n{doc.page_content}",
        metadata=doc.metadata,
    )


def csv_hash(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def get_saved_hash(path: str) -> str:
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE) as f:
            return json.load(f).get(path, "")
    return ""


def save_hash(path: str, digest: str) -> None:
    os.makedirs(INDEX_DIR, exist_ok=True)
    data: dict[str, str] = {}
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE) as f:
            data = json.load(f)
    data[path] = digest
    with open(HASH_FILE, "w") as f:
        json.dump(data, f)


def get_embeddings() -> HuggingFaceEmbeddings:
    global embeddings
    if embeddings is None:
        print("Loading embedding model…")
        embeddings = HuggingFaceEmbeddings(
            model_name="sentence-transformers/all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"batch_size": 64, "normalize_embeddings": True},
        )
        print("Embedding model ready.")
    return embeddings


def build_or_load_index(csv_path: str) -> FAISS:
    global record_count
    emb = get_embeddings()
    digest = csv_hash(csv_path)

    if get_saved_hash(csv_path) == digest and os.path.exists(INDEX_DIR):
        print(f"Cache hit — loading existing index for {csv_path}")
        vs = FAISS.load_local(INDEX_DIR, emb, allow_dangerous_deserialization=True)
        record_count = vs.index.ntotal
        print(f"Index loaded: {record_count} vectors")
        return vs

    print(f"Building index for {csv_path}…")
    loader = CSVLoader(file_path=csv_path, encoding="utf-8")
    raw_docs = loader.load()
    record_count = len(raw_docs)
    print(f"Loaded {record_count} rows — embedding…")

    docs = [enrich_doc(d) for d in raw_docs]
    vs = FAISS.from_documents(docs, emb)
    vs.save_local(INDEX_DIR)
    save_hash(csv_path, digest)
    print(f"Index built and saved ({record_count} vectors).")
    return vs


def _format_docs(docs: list[Document]) -> str:
    print(f"[RAG] Retrieved {len(docs)} chunks for this query")
    if not docs:
        if vectorstore is None:
            return "No relevant records found in the dataset."
        fallback = vectorstore.similarity_search_with_score(_last_query[0] if _last_query else "", k=4)
        print(f"[RAG] Scores of top-4: {[round(s, 3) for _, s in fallback]}")
        if fallback:
            return "\n\n--- RECORD ---\n\n".join(d.page_content for d, _ in fallback)
        return "No relevant records found in the dataset."
    return "\n\n--- RECORD ---\n\n".join(d.page_content for d in docs)


def retrieve_context(question: str) -> str:
    global retriever
    if retriever is None or vectorstore is None:
        return "No relevant records found in the dataset."
    _last_query[0] = question
    docs = retriever.invoke(question)
    return _format_docs(docs)


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            return json.loads(match.group(0))
        raise


def parse_structured_answer(raw: str) -> ChatResponse:
    try:
        payload = _extract_json(raw)
        projects = [ProjectOut.model_validate(p) for p in payload.get("projects", [])]
        summary = str(payload.get("summary", "")).strip()
        if not summary and not projects:
            summary = "I don't have records on that."
        return ChatResponse(summary=summary, projects=projects, answer=summary)
    except Exception:
        return ChatResponse(
            summary=raw.strip(),
            projects=[],
            answer=raw.strip(),
        )


def build_rag_chain(csv_path: str) -> None:
    global vectorstore, retriever, llm

    vectorstore = build_or_load_index(csv_path)
    retriever = vectorstore.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"k": 6, "score_threshold": 0.15},
    )

    llm = ChatGroq(
        model="llama-3.1-8b-instant",
        groq_api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.0,
        max_tokens=1500,
    )
    print("RAG chain ready!")


def answer_question(question: str) -> ChatResponse:
    if llm is None or vectorstore is None:
        raise HTTPException(503, "No dataset loaded. Use Load CSV in the UI first.")

    context = retrieve_context(question)
    prompt = PromptTemplate(template=PROMPT_TEMPLATE, input_variables=["context", "question"])
    chain = (
        {"context": lambda _: context, "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )
    raw = chain.invoke(question)
    result = parse_structured_answer(raw)
    print(f"[ANSWER] summary={result.summary[:120]!r} projects={len(result.projects)}")
    return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.path.exists(DEFAULT_CSV_PATH):
        build_rag_chain(DEFAULT_CSV_PATH)
    else:
        print(f"No CSV at {DEFAULT_CSV_PATH} — upload via /load or add data.csv")
    yield


app = FastAPI(title="Engage Estero RAG API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "chain_ready": llm is not None, "record_count": record_count}


@app.get("/ready")
def ready():
    if llm is None or vectorstore is None:
        raise HTTPException(503, "Index not loaded")
    return {"status": "ready", "record_count": record_count}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    try:
        return answer_question(req.question)
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e)) from e


@app.post("/load")
async def load_csv(file: UploadFile = File(...)):
    os.makedirs(DATA_DIR, exist_ok=True)
    dest = os.path.join(DATA_DIR, file.filename or "upload.csv")
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        build_rag_chain(dest)
        return {"message": f"Loaded {record_count} records from {file.filename}"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e)) from e
