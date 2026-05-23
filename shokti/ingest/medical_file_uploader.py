"""
Upload and import medical Qbank PDF chunks to Gemini File Search store.

Usage:
    python shokti/ingest/medical_file_uploader.py

Uploads 7 medical Qbank chunk PDFs to Gemini Files API, then imports
them into the configured medical File Search store.

This is Phase 1 of the medical question bank extraction pipeline.
"""

import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from google import genai

from shokti.core.config import GEMINI

ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"
sys.path.insert(0, str(ROOT_DIR))


API_BASE = "https://generativelanguage.googleapis.com/v1beta"
MEDICAL_CHUNKS_DIR = ROOT_DIR / "books" / "medical_qbank_chunks"
MAX_WORKERS = 6

MEDICAL_STORE_DISPLAY_NAME = GEMINI.STORE_DISPLAY_NAME
MEDICAL_STORE_NAME = GEMINI.STORE_NAME


def log(msg):
    print(msg, flush=True)


def load_gemini_api_key(env_file):
    """Read GEMINI_API_KEY from a .env file (supports quoted values)."""
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == "GEMINI_API_KEY":
            return value.strip().strip("\"'")
    raise RuntimeError(f"GEMINI_API_KEY not found in {env_file}")


def collect_pdf_files():
    """Return sorted list of all medical Qbank PDF chunks."""
    return sorted(MEDICAL_CHUNKS_DIR.glob("*.pdf"))


STORE_NAME_FILE = ROOT_DIR / ".store_name_medical"


def _save_store_name(name):
    """Persist the store resource name."""
    STORE_NAME_FILE.write_text(name.strip() + "\n")


def find_or_create_store(client):
    """Look up existing medical FileSearchStore or create a new one."""
    for s in client.file_search_stores.list():
        if s.display_name == MEDICAL_STORE_DISPLAY_NAME:
            log(f"Using existing store: {s.name}")
            _save_store_name(s.name)
            return s
    store = client.file_search_stores.create(
        config={
            "display_name": MEDICAL_STORE_DISPLAY_NAME,
            "embedding_model": f"models/gemini-embedding-2",
        }
    )
    log(f"Created store: {store.name}")
    _save_store_name(store.name)
    return store


def get_existing_uploaded_files(client):
    """Build a dict of display_name -> File for all files already in Files API."""
    by_name = {}
    for f in client.files.list():
        if f.display_name:
            by_name[f.display_name] = f
    return by_name


def upload_missing_files(client, pdf_files, existing_by_name):
    """Upload PDF chunks that aren't already in the Files API."""
    to_upload = [p for p in pdf_files if p.name not in existing_by_name]
    already_have = [existing_by_name[p.name] for p in pdf_files if p.name in existing_by_name]

    if already_have:
        log(f"\nFound {len(already_have)} chunks already in Files API, skipping upload")
    if to_upload:
        log(f"\nPhase 1: Uploading {len(to_upload)} missing chunks...")

    uploaded_files = list(already_have)
    for i, pdf_path in enumerate(to_upload):
        size_mb = pdf_path.stat().st_size / (1024 * 1024)
        print(f"  [{i+1}/{len(to_upload)}] {pdf_path.name} ({size_mb:.0f}MB)...", end=" ", flush=True)
        uploaded = client.files.upload(
            file=str(pdf_path),
            config={"display_name": pdf_path.name},
        )
        uploaded_files.append(uploaded)
        print(f"✅ {uploaded.name}", flush=True)

    return uploaded_files


def _request_with_retry(method, url, **kwargs):
    """Make an HTTP request with retries on transient DNS/network errors."""
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        try:
            r = requests.request(method, url, **kwargs)
            return r
        except requests.exceptions.ConnectionError as e:
            if attempt < max_attempts:
                wait = 2 ** attempt
                log(f"  [retry {attempt}/{max_attempts}] DNS/network error, waiting {wait}s...")
                time.sleep(wait)
            else:
                raise


def import_files_parallel(store_name, uploaded_files, headers):
    """Import files into the store using raw REST API (SDK has polling issues)."""
    log(f"\nPhase 2: Importing {len(uploaded_files)} files into store ({MAX_WORKERS} at a time)...")

    def do_import(f):
        name = f.name
        dn = f.display_name or name
        try:
            r = _request_with_retry(
                "POST",
                f"{API_BASE}/{store_name}:importFile",
                headers=headers,
                json={"fileName": name},
                timeout=60,
            )
            if r.status_code == 200:
                return (dn, "✅")
            return (dn, f"❌ {r.status_code}: {r.text[:120]}")
        except Exception as e:
            return (dn, f"❌ {e}")

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(do_import, f): f for f in uploaded_files}
        for i, future in enumerate(as_completed(futures)):
            name, status = future.result()
            print(f"  [{i+1}/{len(uploaded_files)}] {name}... {status}", flush=True)


def list_store_documents(store_name, headers):
    """List documents in store via raw REST API."""
    log(f"\nDocuments in store ({store_name}):")
    page_token = None
    while True:
        params = {"pageSize": 20}
        if page_token:
            params["pageToken"] = page_token
        try:
            r = _request_with_retry(
                "GET",
                f"{API_BASE}/{store_name}/documents",
                headers=headers,
                params=params,
                timeout=30,
            )
            data = r.json()
            for doc in data.get("documents", []):
                log(f"  - {doc.get('displayName') or doc.get('name', '?')}")
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        except Exception as e:
            log(f"  (error listing documents: {e})")
            break


def main():
    pdf_files = collect_pdf_files()
    if not pdf_files:
        raise RuntimeError(f"No PDF chunks found in {MEDICAL_CHUNKS_DIR}")

    api_key = load_gemini_api_key(ENV_FILE)
    client = genai.Client(api_key=api_key, vertexai=False)
    headers = {"X-Goog-Api-Key": api_key, "Content-Type": "application/json"}

    store = find_or_create_store(client)
    store_name = store.name

    existing = get_existing_uploaded_files(client)
    uploaded_files = upload_missing_files(client, pdf_files, existing)

    import_files_parallel(store_name, uploaded_files, headers)

    log(f"\nDone! Store: {store_name}")
    list_store_documents(store_name, headers)


if __name__ == "__main__":
    main()