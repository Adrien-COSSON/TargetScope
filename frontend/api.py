# frontend/api.py
"""HTTP client for the TargetScope API (FastAPI backend).

The frontend never imports the backend: it talks to it over HTTP, so the two
can run in separate containers (docker compose) exactly as in development.

Results are cached with st.cache_data: Streamlit reruns the whole script on
every click, and an assessment takes 5 to 45 s. The same target and
indication are never assessed twice within the TTL.
"""

import os

import httpx
import streamlit as st

API_URL = os.getenv("TARGETSCOPE_API_URL", "http://localhost:8000")
# An assessment can take ~45 s when a source is slow (per-source timeout),
# then the response is built: leave a margin
ASSESS_TIMEOUT = 120.0
DEFAULT_TIMEOUT = 30.0
CACHE_TTL = 3600  # seconds


class ApiError(Exception):
    """The API answered with an error, or could not be reached."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def _request(method: str, path: str, timeout: float = DEFAULT_TIMEOUT, **kwargs):
    try:
        response = httpx.request(method, f"{API_URL}{path}", timeout=timeout, **kwargs)
    except httpx.TransportError as e:
        raise ApiError(f"TargetScope API unreachable at {API_URL} ({type(e).__name__})") from e
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ApiError(str(detail), response.status_code)
    return response.json()


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def indications(query: str) -> dict:
    """Resolved target + therapeutic areas and diseases for the indication menus."""
    return _request("GET", f"/api/targets/{query}/indications")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def search_diseases(text: str) -> list[dict]:
    return _request("GET", "/api/diseases/search", params={"q": text})


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def assess(query: str, indication_id: str | None) -> dict:
    """{"assessment": TargetAssessment, "raw": {source: result}}."""
    return _request(
        "POST",
        "/api/assess",
        timeout=ASSESS_TIMEOUT,
        json={"query": query, "indication_id": indication_id},
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def synthesis(assessment: dict) -> dict | None:
    """Claude synthesis, None when no API key is configured or the call failed."""
    return _request(
        "POST", "/api/synthesis", timeout=ASSESS_TIMEOUT, json={"assessment": assessment}
    )


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def engine_config() -> dict:
    return _request("GET", "/api/engine/config")


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def structure_file(url: str) -> str | None:
    """Text of a structure file (AlphaFold or RCSB), for the 3D viewer.

    Fetched here rather than in the browser: no CORS surprise, and cached.
    """
    try:
        response = httpx.get(url, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        return response.text
    except httpx.HTTPError:
        return None
