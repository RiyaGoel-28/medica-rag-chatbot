import argparse
import hashlib
import os
import time
from pathlib import Path

from dotenv import load_dotenv
from werkzeug.utils import secure_filename

from document_registry import initialize_registry, upsert_document
from medical_rag import ConfigurationError, _get_embeddings, _required_settings
from user_identity import user_key, user_namespace

MAX_UPLOAD_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 200


def _stable_chunk_ids(chunks):
    ids = []
    for position, chunk in enumerate(chunks):
        metadata = chunk.metadata or {}
        source = metadata.get("document_id") or os.path.normcase(
            os.path.normpath(str(metadata.get("source", "")))
        )
        page = metadata.get("page", "unknown")
        identity = f"{source}:{page}:{position}".encode("utf-8")
        ids.append(hashlib.sha256(identity).hexdigest())
    return ids


def _index_pages(pages, settings, namespace=None):
    from langchain_pinecone import PineconeVectorStore
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=20)
    chunks = splitter.split_documents(pages)
    if not chunks:
        raise ValueError("The selected PDF contained no extractable text.")
    chunk_ids = _stable_chunk_ids(chunks)

    embeddings = _get_embeddings(
        os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2"),
        os.getenv("EMBEDDING_DEVICE", "cpu"),
    )
    PineconeVectorStore.from_documents(
        documents=chunks,
        ids=chunk_ids,
        embedding=embeddings,
        index_name=settings["PINECONE_INDEX_NAME"],
        pinecone_api_key=settings["PINECONE_API_KEY"],
        namespace=namespace,
    )
    return len(chunks)


def ingest_uploaded_pdf(filename, content, upload_dir, namespace):
    display_name = secure_filename(filename or "")
    if not display_name or Path(display_name).suffix.lower() != ".pdf":
        raise ValueError("Choose a PDF file to upload.")
    if not content:
        raise ValueError("The selected PDF is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValueError("PDF files must be 15 MB or smaller.")
    if not content.startswith(b"%PDF-"):
        raise ValueError("The uploaded file does not appear to be a valid PDF.")

    settings = _required_settings(require_groq=False)
    document_id = hashlib.sha256(content).hexdigest()
    stored_path = Path(upload_dir) / namespace / f"{document_id}.pdf"
    stored_path.parent.mkdir(parents=True, exist_ok=True)
    if not stored_path.exists():
        stored_path.write_bytes(content)

    try:
        from langchain_community.document_loaders import PyPDFLoader
        from pypdf import PdfReader

        reader = PdfReader(str(stored_path))
        if reader.is_encrypted:
            raise ValueError("Password-protected PDFs are not supported.")
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ValueError(f"PDF files must have {MAX_PDF_PAGES} pages or fewer.")
        pages = PyPDFLoader(str(stored_path)).load()
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("The PDF could not be read. Check that it is a valid, text-based PDF.") from exc

    for page in pages:
        page.metadata["source"] = display_name
        page.metadata["document_id"] = document_id
        try:
            retention_days = int(os.getenv("RETENTION_DAYS", "90"))
        except ValueError as exc:
            raise ValueError("RETENTION_DAYS must be an integer from 1 to 3650.") from exc
        if retention_days < 1 or retention_days > 3650:
            raise ValueError("RETENTION_DAYS must be an integer from 1 to 3650.")
        page.metadata["expires_at"] = time.time() + retention_days * 86400
    chunks = _index_pages(pages, settings, namespace)
    return {
        "document_id": document_id,
        "stored_path": str(stored_path),
        "filename": display_name,
        "chunks": chunks,
    }


def ingest_directory(directory, auth0_subject, upload_root, database_path):
    load_dotenv()
    settings = _required_settings(require_groq=False)
    pdf_paths = sorted(Path(directory).glob("*.pdf"))
    if not pdf_paths:
        raise FileNotFoundError(f"No PDF files found in {directory}")
    namespace = user_namespace(auth0_subject)
    owner = user_key(auth0_subject)
    initialize_registry(database_path)
    total_chunks = 0
    for path in pdf_paths:
        result = ingest_uploaded_pdf(path.name, path.read_bytes(), upload_root, namespace)
        upsert_document(
            database_path,
            owner,
            namespace,
            result["document_id"],
            result["filename"],
            result["stored_path"],
            result["chunks"],
            int(os.getenv("RETENTION_DAYS", "90")),
        )
        total_chunks += result["chunks"]
    return total_chunks


def main():
    parser = argparse.ArgumentParser(description="Index reference PDFs into Pinecone.")
    parser.add_argument("directory", nargs="?", default="data/references")
    parser.add_argument(
        "--auth0-subject",
        required=True,
        help="Authenticated user's Auth0 subject; indexing is isolated to its derived namespace",
    )
    args = parser.parse_args()
    try:
        count = ingest_directory(
            args.directory,
            args.auth0_subject,
            Path("data/uploads"),
            Path("instance/documents.sqlite3"),
        )
    except (ConfigurationError, FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    print(f"Indexed {count} text chunks from {args.directory}.")


if __name__ == "__main__":
    main()