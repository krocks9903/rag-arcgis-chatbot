// API base for the standalone admin page (outside the Vite bundle, so it
// can't read import.meta.env.VITE_API_BASE like the React app does).
//
// The frontend (Vercel) and backend (Cloud Run) are deployed as two separate
// origins — there is no "same origin" case here, unlike a single-container
// setup where nginx/FastAPI serve both from one host. PROD_API_BASE must be
// kept in sync with the VITE_API_BASE Vercel env var (see frontend-react/
// .env.example and the deploy notes) whenever the backend URL changes.
(function () {
  if (typeof window === "undefined") return;
  const host = window.location.hostname;
  const port = window.location.port;
  const local = host === "localhost" || host === "127.0.0.1";

  const PROD_API_BASE = "https://rag-arcgis-chatbot-830570926329.us-central1.run.app";

  if (local && port === "5173") {
    // Vite dev server → local uvicorn default
    window.API_BASE = "http://localhost:8000";
  } else {
    window.API_BASE = PROD_API_BASE;
  }
})();
