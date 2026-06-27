"""
RAG Chatbot Backend - LangChain + FAISS (free, local)
Uses: HuggingFace embeddings (free) + FAISS vector store (free, local)
LLM: Uses Ollama locally OR HuggingFace Inference API (free tier)
"""

import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from langchain_community.document_loaders import CSVLoader
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain.chains import RetrievalQA
from langchain_community.llms import HuggingFaceHub
from langchain.prompts import PromptTemplate

app = FastAPI(title="RAG ArcGIS Chatbot API")

# Allow frontend requests
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Global state ---
qa_chain = None
vectorstore = None

# -------------------------------------------------------
# MODELS
# -------------------------------------------------------

class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = "default"

class ChatResponse(BaseModel):
    answer: str
    sources: list[str] = []

class LoadRequest(BaseModel):
    csv_path: str  # relative to /data folder, e.g. "my_data.csv"

# -------------------------------------------------------
# RAG SETUP
# -------------------------------------------------------

def build_rag_chain(csv_path: str):
    """Load CSV, embed with HuggingFace (free), store in FAISS (local)."""
    global qa_chain, vectorstore

    print(f"Loading CSV: {csv_path}")
    loader = CSVLoader(file_path=csv_path, encoding="utf-8")
    documents = loader.load()

    # Split into chunks
    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)
    chunks = splitter.split_documents(documents)
    print(f"Created {len(chunks)} chunks from {len(documents)} rows")

    # Free embeddings via HuggingFace (runs locally, no API key needed)
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2"
    )

    # Local FAISS vector store (no cost, no external service)
    vectorstore = FAISS.from_documents(chunks, embeddings)
    vectorstore.save_local("faiss_index")
    print("FAISS index built and saved.")

    # LLM: HuggingFace Hub free inference
    # Get a free token at https://huggingface.co/settings/tokens
    hf_token = os.getenv("HUGGINGFACEHUB_API_TOKEN")
    if not hf_token:
        raise ValueError("Set HUGGINGFACEHUB_API_TOKEN in your .env file")

    llm = HuggingFaceHub(
        repo_id="mistralai/Mistral-7B-Instruct-v0.2",  # free model
        huggingfacehub_api_token=hf_token,
        model_kwargs={"temperature": 0.3, "max_new_tokens": 512},
    )

    # Prompt template
    prompt_template = """You are a helpful assistant. Use the context below to answer the question.
If you don't know the answer from the context, say so clearly.

Context:
{context}

Question: {question}

Answer:"""

    PROMPT = PromptTemplate(
        template=prompt_template, input_variables=["context", "question"]
    )

    # Build retrieval chain
    qa_chain = RetrievalQA.from_chain_type(
        llm=llm,
        chain_type="stuff",
        retriever=vectorstore.as_retriever(search_kwargs={"k": 4}),
        return_source_documents=True,
        chain_type_kwargs={"prompt": PROMPT},
    )
    print("RAG chain ready!")


# -------------------------------------------------------
# ROUTES
# -------------------------------------------------------

@app.on_event("startup")
async def startup():
    """Auto-load the default CSV on startup if it exists."""
    default_csv = "data/data.csv"
    if os.path.exists(default_csv):
        try:
            build_rag_chain(default_csv)
        except Exception as e:
            print(f"Warning: Could not load default CSV: {e}")


@app.post("/load", summary="Load or reload a CSV into the vector store")
async def load_csv(req: LoadRequest):
    path = f"data/{req.csv_path}"
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"File not found: {path}")
    try:
        build_rag_chain(path)
        return {"status": "ok", "message": f"Loaded {path} into vector store"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/chat", response_model=ChatResponse, summary="Ask a question")
async def chat(req: ChatRequest):
    if qa_chain is None:
        raise HTTPException(
            status_code=503,
            detail="No data loaded yet. POST /load with a CSV path first.",
        )
    try:
        result = qa_chain({"query": req.question})
        answer = result["result"]

        # Extract source rows for display
        sources = []
        for doc in result.get("source_documents", []):
            src = doc.page_content[:200]
            if src not in sources:
                sources.append(src)

        return ChatResponse(answer=answer, sources=sources[:3])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/health")
async def health():
    return {"status": "ok", "index_loaded": qa_chain is not None}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
