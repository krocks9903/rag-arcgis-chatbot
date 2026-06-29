# RAG ArcGIS Chatbot

This app combines a FastAPI backend with a simple frontend so you can ask questions about your CSV data and view the results alongside an ArcGIS map.

## What you need

- Python 3.10 or 3.11
- A Groq API key for the LLM backend
- Optional: an ArcGIS API key if you want to replace the default map layer

## Quick start

### 1. Clone the repo

```bash
git clone https://github.com/krocks9903/rag-arcgis-chatbot.git
cd rag-arcgis-chatbot
```

### 2. Create and activate a virtual environment

On macOS/Linux:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
```

On Windows PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

This installs FastAPI, Uvicorn, LangChain, FAISS, sentence-transformers, and the Groq/Hugging Face integrations needed by the app.

### 4. Create your environment file

```bash
copy .env.example .env
```

Then edit the new file and add your Groq key:

```env
GROQ_API_KEY=your_groq_api_key_here
```

You can also leave the ArcGIS key blank unless you want to swap in your own map layer.

### 5. Add or use your data

The app looks for a CSV at:

```text
backend/data/data.csv
```

You can either:

- replace that file with your own CSV, or
- upload a CSV through the app UI if the endpoint is available

### 6. Run the backend

```bash
uvicorn app:app --reload --port 8000
```

Once it starts, open:

- API docs: http://localhost:8000/docs
- Frontend: open frontend/index.html in your browser, or serve the folder with a simple server

### 7. Open the frontend

From the repo root:

```bash
cd frontend
python -m http.server 3000
```

Then open http://localhost:3000 in your browser.

## Project structure

```text
rag-arcgis-chatbot/
├── backend/
│   ├── app.py
│   ├── requirements.txt
│   ├── .env.example
│   └── data/
├── frontend/
│   └── index.html
└── README.md
```

## Notes

- The app uses a local FAISS index and local embeddings, so it can run without a hosted vector database.
- The first run may take a little longer while the embedding model downloads.
- If you want to use a different map layer, update the ArcGIS configuration in the frontend HTML.
