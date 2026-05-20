"""Diagnose import_file — check operation details."""
import time
from google import genai
from pathlib import Path

api_key = open(".env").read().split("GEMINI_API_KEY=")[1].split("\n")[0].strip("\"'")
client = genai.Client(api_key=api_key, http_options={"timeout": 600000})

store = None
for s in client.file_search_stores.list():
    if s.display_name == "biology-hasan-sir":
        store = s
        break
print(f"Store: {store.name}")

# Use already-uploaded file from Phase 1
print(f"\nChecking for existing files in Files API...", flush=True)
files = list(client.files.list())
print(f"Files in API: {len(files)}")
if files:
    f = files[0]
    print(f"Using file: {f.name} ({f.display_name}), state={f.state}")
    
    # Check if it's already in the store
    docs = list(client.file_search_stores.documents.list(parent=store.name))
    print(f"Documents already in store: {len(docs)}")
    for d in docs[:3]:
        print(f"  - {d.display_name or d.name}")

    # Try import_file
    print(f"\nCalling import_file for {f.name}...", flush=True)
    op = client.file_search_stores.import_file(
        file_search_store_name=store.name,
        file_name=f.name,
    )
    print(f"Operation: {op.name}, done={op.done}", flush=True)
    if op.metadata:
        print(f"Metadata: {op.metadata}", flush=True)
    if op.error:
        print(f"Initial error: {op.error}", flush=True)
    
    start = time.time()
    while not op.done:
        elapsed = time.time() - start
        if elapsed > 10 and elapsed < 15:
            print(f"  {elapsed:.0f}s elapsed, still waiting...", flush=True)
        if elapsed > 60:
            print(f"TIMEOUT at 60s", flush=True)
            # Check operation metadata
            print(f"  Checking operation status...", flush=True)
            op = client.operations.get(op)
            print(f"  After get: done={op.done}, error={op.error}", flush=True)
            if op.metadata:
                print(f"  Metadata: {op.metadata}", flush=True)
            if hasattr(op, 'response') and op.response:
                print(f"  Response: {op.response}", flush=True)
            break
        time.sleep(5)
        op = client.operations.get(op)
    
    if op.done:
        print(f"Done! error={op.error}", flush=True)
        if op.response:
            print(f"Response: {op.response}", flush=True)
