import time
from pathlib import Path

from google import genai
from google.genai import errors

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT_DIR / ".env"
BOOKS_DIR = ROOT_DIR / "books"
CHAPTERS_DIR = BOOKS_DIR / "chapters"
STORE_NAME = "biology-hasan-sir"
PDFS_TO_UPLOAD = {
    "chapter_06_bryophyta_and_pteridophyta_pages_198-208.pdf",
    "chapter_08_tissue_and_tissue_system_pages_235-254.pdf",
}


def log(message):
    print(message, flush=True)


def load_gemini_api_key(env_file):
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        if key.strip() == "GEMINI_API_KEY":
            return value.strip().strip("\"'")

    raise RuntimeError(f"GEMINI_API_KEY was not found in {env_file}")


def collect_pdf_files():
    if CHAPTERS_DIR.exists():
        chapter_pdfs = sorted(CHAPTERS_DIR.glob("*/*.pdf"))
        if chapter_pdfs:
            return [pdf for pdf in chapter_pdfs if pdf.name in PDFS_TO_UPLOAD]

    return [pdf for pdf in sorted(BOOKS_DIR.glob("*.pdf")) if pdf.name in PDFS_TO_UPLOAD]


def upload_pdf(client, store_name, pdf_path):
    for attempt in range(1, 4):
        try:
            log(f"  Starting upload attempt {attempt}/3...")
            operation = client.file_search_stores.upload_to_file_search_store(
                file=pdf_path,
                file_search_store_name=store_name,
                config={
                    "display_name": pdf_path.name,
                    "mime_type": "application/pdf",
                },
            )

            while not operation.done:
                log("  Waiting for Gemini indexing operation...")
                time.sleep(10)
                operation = client.operations.get(operation)

            if operation.error:
                raise RuntimeError(
                    f"Gemini failed to index {pdf_path.name}: {operation.error}"
                )

            return
        except errors.ClientError as exc:
            if "Upload has already been terminated" not in str(exc) or attempt == 3:
                raise

            wait_seconds = attempt * 20
            log(
                f"Upload session expired for {pdf_path.name}; "
                f"retrying in {wait_seconds}s ({attempt}/3)..."
            )
            time.sleep(wait_seconds)


def main():
    pdf_files = collect_pdf_files()
    if not pdf_files:
        raise RuntimeError(f"No PDF files were found in {BOOKS_DIR}")

    client = genai.Client(api_key=load_gemini_api_key(ENV_FILE), vertexai=False)
    store = client.file_search_stores.create(
        config={
            "display_name": STORE_NAME,
            "embedding_model": "models/gemini-embedding-2",
        }
    )

    log(f"Created store: {store.name}")
    log(f"Uploading {len(pdf_files)} PDF file(s).")

    for pdf_path in pdf_files:
        log(f"Uploading {pdf_path.name}...")
        upload_pdf(client, store.name, pdf_path)
        log(f"Uploaded {pdf_path.name}")

    log("Documents in store:")
    for document in client.file_search_stores.documents.list(parent=store.name):
        log(f"- {document.display_name or document.name}")

    log("All files uploaded successfully!")
    log(f"Store name: {store.name}")


if __name__ == "__main__":
    main()
