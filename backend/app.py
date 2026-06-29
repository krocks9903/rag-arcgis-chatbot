import os
import hashlib
import json
import traceback
import shutil
from dotenv import load_dotenv

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(BACKEND_DIR, ".env"))

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from langchain_community.document_loaders import CSVLoader
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_groq import ChatGroq
from langchain.prompts import PromptTemplate
from langchain.schema import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_core.runnables import RunnablePassthrough

app = FastAPI(title="Engage Estero RAG API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ── Globals ───────────────────────────────────────────────────────────────────
vectorstore  = None
rag_chain    = None
embeddings   = None
record_count = 0

INDEX_DIR = os.path.join(BACKEND_DIR, "faiss_index")
HASH_FILE = os.path.join(INDEX_DIR, "csv_hash.json")
DATA_DIR = os.path.join(BACKEND_DIR, "data")
DEFAULT_CSV_PATH = os.path.join(DATA_DIR, "data.csv")

# ── Prompt ────────────────────────────────────────────────────────────────────
PROMPT_TEMPLATE = """You are a helpful assistant for the Village of Estero's Engage Estero platform.
You help residents understand Planning, Zoning & Design Board decisions using official meeting records.

RULES — follow exactly:
1. Only use facts from the Context. Never invent any detail.
2. If no relevant info exists, say: "I don't have records on that."
3. Write plain English. No jargon, no markdown bold, no asterisks.
4. For EVERY matching project output this block — the delimiters must be EXACTLY as shown:

START_PROJECT
Title: [ProjectName from context]
ID: [ApplicationID from context]
Location: [Location from context]
Summary: [1-2 sentences about what this project is and what happened]
Status: [Approved / Denied / Continued / No decision recorded]
Date: [MeetingDate from context]
DocumentURL: [Document_Link from context — copy exactly, do not invent]
END_PROJECT

5. Output ONLY the blocks above, one per project. No other text except a single closing sentence after all blocks.

Context:
{context}

Question: {question}

Answer:"""


# ── Helpers ───────────────────────────────────────────────────────────────────
def enrich_doc(doc: Document) -> Document:
    """
    Build a tight, keyword-rich search header so FAISS can match on
    business name, address, app ID, and outcome — even when the full
    ProjectName field is 250+ chars of boilerplate.
    """
    import re
    lines = doc.page_content.strip().split("\n")
    fields = {}
    for line in lines:
        if ": " in line:
            k, v = line.split(": ", 1)
            fields[k.strip()] = v.strip()

    raw_name   = fields.get("ProjectName", "")
    location   = fields.get("Location", "") or fields.get("LocationName", "")
    app_id     = fields.get("ApplicationID", "")
    date       = fields.get("MeetingDate", "")
    outcome    = fields.get("Outcome", "")
    action     = fields.get("ActionTaken", "")
    status     = fields.get("Status", "")

    # Extract the clean short business/project name from the long ProjectName
    # Pattern: "Name (DOS/DCI/LDO-YYYY-EXXX) (District N) address..."
    short_name = re.split(r"\s*\((?:DOS|DCI|LDO|ADD|CPA|REZ)\d{4}", raw_name)[0].strip()
    short_name = re.sub(r"\s*-\s*Development Order.*", "", short_name, flags=re.IGNORECASE).strip()
    short_name = short_name[:80]  # cap at 80 chars

    # Outcome: first 60 chars is usually "Approved..." or "Denied..."
    outcome_short = (outcome or action or status)[:60]

    # Build a dense, searchable header — this is what FAISS actually matches on
    header_parts = filter(None, [
        short_name,          # e.g. "Wawa Convenience Food & Beverage Store with Gas"
        app_id,              # e.g. "DOS2022-E016"
        location,            # e.g. "10081 Estero Town Commons Place"
        outcome_short,       # e.g. "Approved the application with staff conditions"
        date,                # e.g. "8/22/2023"
    ])
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


def save_hash(path: str, h: str):
    os.makedirs(INDEX_DIR, exist_ok=True)
    data = {}
    if os.path.exists(HASH_FILE):
        with open(HASH_FILE) as f:
            data = json.load(f)
    data[path] = h
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


# ── Core: build or load FAISS index ──────────────────────────────────────────
def build_or_load_index(csv_path: str) -> FAISS:
    global record_count
    emb  = get_embeddings()
    h    = csv_hash(csv_path)

    # Cache hit — same file hash, reload instantly
    if get_saved_hash(csv_path) == h and os.path.exists(INDEX_DIR):
        print(f"Cache hit — loading existing index for {csv_path}")
        vs = FAISS.load_local(INDEX_DIR, emb, allow_dangerous_deserialization=True)
        record_count = vs.index.ntotal
        print(f"Index loaded: {record_count} vectors")
        return vs

    # New/changed file — embed in batches
    print(f"Building index for {csv_path}…")
    loader   = CSVLoader(file_path=csv_path, encoding="utf-8")
    raw_docs = loader.load()
    record_count = len(raw_docs)
    print(f"Loaded {record_count} rows — embedding in batches…")

    docs = [enrich_doc(d) for d in raw_docs]
    vs   = FAISS.from_documents(docs, emb)
    vs.save_local(INDEX_DIR)
    save_hash(csv_path, h)
    print(f"Index built and saved ({record_count} vectors).")
    return vs


# ── Core: assemble RAG chain ──────────────────────────────────────────────────
def build_rag_chain(csv_path: str):
    global vectorstore, rag_chain

    vectorstore = build_or_load_index(csv_path)

    # Score threshold: only pass chunks with cosine similarity > 0.35
    # This prevents the LLM from seeing unrelated records and hallucinating
    # Primary: score-threshold retriever (filters irrelevant chunks)
    # Fallback: plain similarity if threshold returns nothing
    ret = vectorstore.as_retriever(
        search_type="similarity_score_threshold",
        search_kwargs={"k": 6, "score_threshold": 0.15},
    )

    llm = ChatGroq(
        model="llama-3.1-8b-instant",
        groq_api_key=os.getenv("GROQ_API_KEY"),
        temperature=0.0,    # 0 = maximally factual
        max_tokens=1500,
    )

    prompt = PromptTemplate(
        template=PROMPT_TEMPLATE,
        input_variables=["context", "question"],
    )

    def format_docs(docs):
        print(f"[RAG] Retrieved {len(docs)} chunks for this query")
        if not docs:
            # Last resort: grab top 4 by pure similarity, no threshold
            fallback = vectorstore.similarity_search_with_score(last_query[0] if last_query else "", k=4)
            print(f"[RAG] Scores of top-4: {[round(s,3) for _,s in fallback]}")
            if fallback:
                return "\n\n--- RECORD ---\n\n".join(d.page_content for d,_ in fallback)
            return "No relevant records found in the dataset."
        return "\n\n--- RECORD ---\n\n".join(d.page_content for d in docs)

    last_query = [""]  # mutable container to share query with format_docs

    def track_and_retrieve(q):
        last_query[0] = q
        return ret.invoke(q)

    rag_chain = (
        {"context": RunnablePassthrough() | track_and_retrieve | format_docs,
         "question": RunnablePassthrough()}
        | prompt
        | llm
        | StrOutputParser()
    )

    print("RAG chain ready!")


# ── Startup ───────────────────────────────────────────────────────────────────
@app.on_event("startup")
def startup():
    if os.path.exists(DEFAULT_CSV_PATH):
        build_rag_chain(DEFAULT_CSV_PATH)
    else:
        print(f"No CSV found at {DEFAULT_CSV_PATH} — place your file there or use Load CSV in the UI.")


# ── Endpoints ─────────────────────────────────────────────────────────────────
class ChatRequest(BaseModel):
    question: str
    session_id: str = "default"


@app.get("/health")
def health():
    return {"status": "ok", "chain_ready": rag_chain is not None, "record_count": record_count}


@app.post("/chat")
def chat(req: ChatRequest):
    if rag_chain is None:
        raise HTTPException(503, "No dataset loaded. Use Load CSV in the UI first.")
    try:
        answer = rag_chain.invoke(req.question)
        answer = answer.replace("```json", "").replace("```", "").strip()
        print(f"[ANSWER] length={len(answer)} preview={repr(answer[:200])}")
        return {"answer": answer}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@app.post("/load")
async def load_csv(file: UploadFile = File(...)):
    os.makedirs("data", exist_ok=True)
    dest = f"data/{file.filename}"
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    try:
        build_rag_chain(dest)
        return {"message": f"Loaded {record_count} records from {file.filename}"}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))