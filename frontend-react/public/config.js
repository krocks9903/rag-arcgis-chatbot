// API base for vanilla admin pages (and any non-Vite static HTML).
// Always set window.API_BASE as a string — including "" for same-origin —
// so admin.js can distinguish "same origin" from "unset".
(function () {
  if (typeof window === "undefined") return;
  const host = window.location.hostname;
  const port = window.location.port;
  const local = host === "localhost" || host === "127.0.0.1";

  if (local && port === "3000") {
    // docker-compose: nginx UI → API on 8080
    window.API_BASE = "http://localhost:8080";
  } else if (local && port === "5173") {
    // Vite dev server → local uvicorn default
    window.API_BASE = "http://localhost:8000";
  } else {
    // Cloud Run / same-container / production: API is this origin
    window.API_BASE = "";
  }
})();
