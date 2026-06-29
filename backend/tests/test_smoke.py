"""Smoke tests for the RAG ArcGIS chatbot backend.

These run without a GROQ_API_KEY or network access: importing the module only
defines the FastAPI app and helpers (the model/index build runs at startup,
which is not triggered by import), so CI can validate wiring cheaply.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as backend_app  # noqa: E402


def test_app_metadata():
    assert backend_app.app.title == "Engage Estero RAG API"


def test_expected_routes_registered():
    paths = {route.path for route in backend_app.app.routes}
    assert {"/health", "/chat", "/load"}.issubset(paths)


def test_csv_hash_is_stable(tmp_path):
    sample = tmp_path / "sample.csv"
    sample.write_text("a,b\n1,2\n", encoding="utf-8")
    first = backend_app.csv_hash(str(sample))
    second = backend_app.csv_hash(str(sample))
    assert first == second and len(first) == 32
