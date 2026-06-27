# RAG Chatbot + ArcGIS Map

A **fully free** RAG (Retrieval-Augmented Generation) chatbot that answers questions from your CSV data, displayed alongside an ArcGIS interactive map.

## 🏗️ Architecture (100% Free)

```
┌─────────────────────────────────────────────────────────┐
│  Frontend (plain HTML)                                  │
│  ┌─────────────────────┐  ┌──────────────────────────┐ │
│  │   Chat Panel        │  │   ArcGIS Map Panel       │ │
│  │   (left)            │  │   (right, ArcGIS JS API) │ │
│  └─────────────────────┘  └──────────────────────────┘ │
└────────────────────┬────────────────────────────────────┘
                     │ REST
┌────────────────────▼────────────────────────────────────┐
│  Backend (FastAPI)                                      │
│                                                         │
│  CSV → LangChain CSVLoader                             │
│      → RecursiveCharacterTextSplitter                  │
│      → HuggingFace Embeddings (local, free)            │
│      → FAISS Vector Store (local, free)                │
│      → HuggingFace Hub LLM (free inference tier)       │
│      → RetrievalQA Chain                               │
└─────────────────────────────────────────────────────────┘
```

**Free components:**
| Component | Tool | Cost |
|---|---|---|
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` | Free (local) |
| Vector Store | FAISS | Free (local) |
| LLM | Mistral-7B via HuggingFace Hub | Free tier |
| Map | ArcGIS JS API 4.x | Free (developer account) |
| Framework | LangChain | Open source |

---

## 🚀 Quick Start

### 1. Clone & set up
```bash
git clone https://github.com/YOUR_USERNAME/rag-arcgis-chatbot.git
cd rag-arcgis-chatbot
```

### 2. Backend setup
```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Set up your free API token
cp .env.example .env
# Edit .env and add your HuggingFace token
# Get one free at: https://huggingface.co/settings/tokens
```

### 3. Add your CSV
```bash
# Put your CSV in the /data folder (or use the sample)
cp your_data.csv ../data/data.csv
```

### 4. Run the backend
```bash
cd backend
uvicorn app:app --reload --port 8000
# → API docs at http://localhost:8000/docs
```

### 5. Open the frontend
```bash
# Just open in your browser — no build step needed
open ../frontend/index.html
# Or serve it:
cd ../frontend && python -m http.server 3000
```

---

## 🗺️ Connecting your ArcGIS Map

1. Create a free developer account at [developers.arcgis.com](https://developers.arcgis.com)
2. Get your API key from the dashboard
3. In `frontend/index.html`, find the `TODO` comment and replace with your Feature Layer URL:

```javascript
const layer = new FeatureLayer({
  url: "https://services.arcgis.com/YOUR_ORG/FeatureServer/0"
});
map.add(layer);
```

4. Update the map center coordinates to your area:
```javascript
center: [-82.4139, 28.0587],  // [longitude, latitude]
zoom: 13,
```

---

## 📂 Project Structure

```
rag-arcgis-chatbot/
├── backend/
│   ├── app.py              # FastAPI + LangChain RAG pipeline
│   ├── requirements.txt    # Python dependencies
│   └── .env.example        # Environment variable template
├── frontend/
│   └── index.html          # Split-panel UI (chat + ArcGIS map)
├── data/
│   └── data.csv            # Your data goes here
└── README.md
```

---

## 🔌 API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Check backend status |
| `POST` | `/load` | Load a CSV into the vector store |
| `POST` | `/chat` | Ask a question, get an answer + sources |

**Example chat request:**
```bash
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Which locations are in the commercial category?"}'
```

---

## 🔄 Swapping the LLM (optional upgrades)

The backend is designed to swap LLMs easily. To use a different free option:

**Option A: Ollama (fully local, no API key)**
```python
from langchain_community.llms import Ollama
llm = Ollama(model="mistral")  # after running: ollama pull mistral
```

**Option B: Groq (free tier, very fast)**
```python
from langchain_groq import ChatGroq
llm = ChatGroq(model="llama3-8b-8192", groq_api_key=os.getenv("GROQ_API_KEY"))
```
