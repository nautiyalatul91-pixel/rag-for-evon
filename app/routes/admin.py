import os
import uuid
import shutil
import tempfile
import hashlib
from typing import List
from fastapi import APIRouter, File, UploadFile, HTTPException, status, Depends

from app.config import logger, audit_logger
from app.models.responses import UploadResponse, UploadStatus, DocumentMetadata, DeleteResponse
from app.services.db_service import db_service
from app.services.parser_service import parser_service
from app.services.chunking_service import chunking_service
from app.services.embedding_service import embedding_service
from app.services.auth_service import require_admin

router = APIRouter(prefix="/admin", tags=["admin"])

def calculate_sha256(file_obj) -> str:
    """Calculate SHA-256 hash of a file object."""
    hasher = hashlib.sha256()
    file_obj.seek(0)
    while chunk := file_obj.read(65536):
        hasher.update(chunk)
    file_obj.seek(0)  # Reset pointer
    return hasher.hexdigest()

@router.post("/upload", response_model=UploadResponse)
def upload_documents(files: List[UploadFile] = File(...), current_user: dict = Depends(require_admin)):
    """
    Ingests one or more documents (PDF, DOCX, XLSX, TXT) synchronously.
    Validates file extension and size (< 20MB) early.
    Prevents duplicate ingestion.
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested upload of %d files.", username, role, len(files))
    
    ALLOWED_EXTENSIONS = {"pdf", "docx", "xlsx", "txt"}
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

    try:
        # 1. Early Validation Phase
        for file in files:
            filename = file.filename or ""
            ext = filename.split(".")[-1].lower() if "." in filename else ""
            if ext not in ALLOWED_EXTENSIONS:
                logger.error("Early validation failed: unsupported extension '%s' for file '%s'", ext, filename)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Unsupported file type: '{ext}'. Allowed types: PDF, DOCX, XLSX, TXT."
                )

            # Check size by seeking to the end of the file stream
            file.file.seek(0, 2)
            file_size = file.file.tell()
            file.file.seek(0)

            if file_size > MAX_FILE_SIZE:
                logger.error("Early validation failed: file '%s' exceeds 20MB limit (%d bytes)", filename, file_size)
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"File '{filename}' exceeds the maximum allowed size of 20MB (Size: {file_size / (1024 * 1024):.2f}MB)."
                )

        uploaded_statuses = []
        failed_statuses = []

        # 2. Processing Phase (Synchronous)
        for file in files:
            filename = file.filename or ""
            logger.info("Processing file: %s", filename)
            
            # We save file to a temporary file on disk first
            suffix = f".{filename.split('.')[-1].lower()}" if "." in filename else ""
            temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
            temp_path = temp_file.name

            doc_id = str(uuid.uuid4())
            
            try:
                # Copy UploadFile content to local temp file
                file.file.seek(0)
                shutil.copyfileobj(file.file, temp_file)
                temp_file.close() # Close to flush content, keep path

                # Calculate content hash
                with open(temp_path, "rb") as f:
                    content_hash = calculate_sha256(f)

                # Check for duplicates (filename or content hash)
                is_duplicate, duplicate_reason = db_service.check_duplicate(filename, content_hash)
                if is_duplicate:
                    logger.warning("Duplicate detected for file %s: %s", filename, duplicate_reason)
                    failed_statuses.append(UploadStatus(
                        filename=filename,
                        status="failure",
                        error=duplicate_reason
                    ))
                    continue

                # Create document record in SQLite (status: processing)
                db_service.create_document_record(doc_id, filename, content_hash)

                # A. Parse the document
                pages = parser_service.parse_file(temp_path, filename)
                if not pages:
                    raise ValueError("No text content could be extracted from the document.")

                # B. Chunk the document
                chunks = chunking_service.chunk_document(pages)
                if not chunks:
                    raise ValueError("Extracted text did not produce any valid chunks.")

                # C. Batch and Generate Embeddings
                chunk_batches = embedding_service.batch_chunks(chunks)
                all_embeddings = []

                for batch in chunk_batches:
                    batch_texts = [c["text"] for c in batch]
                    embeddings = embedding_service.get_embeddings(batch_texts)
                    all_embeddings.extend(embeddings)

                if len(chunks) != len(all_embeddings):
                    raise ValueError(
                        f"Mismatch between number of chunks ({len(chunks)}) and embeddings generated ({len(all_embeddings)})."
                    )

                # D. Store in ChromaDB
                db_service.add_chunks_to_chroma(doc_id, filename, content_hash, chunks, all_embeddings)

                # E. Update status to completed
                db_service.update_document_status(doc_id, "completed", len(chunks))

                uploaded_statuses.append(UploadStatus(
                    filename=filename,
                    status="success",
                    document_id=doc_id,
                    chunks=len(chunks)
                ))
                logger.info("Successfully ingested document: %s (ID: %s)", filename, doc_id)

            except Exception as e:
                logger.error("Failed to ingest document %s: %s", filename, e, exc_info=True)
                try:
                    db_service.update_document_status(doc_id, "failed", 0)
                except Exception as db_err:
                    logger.error("Failed to update status to failed for %s: %s", filename, db_err)

                failed_statuses.append(UploadStatus(
                    filename=filename,
                    status="failure",
                    error=str(e)
                ))
                
            finally:
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except Exception as clean_err:
                        logger.warning("Failed to remove temp file %s: %s", temp_path, clean_err)

        # Audit log the details of the ingestion attempt
        success_files = [u.filename for u in uploaded_statuses]
        failed_files = [f.filename for f in failed_statuses]
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /admin/upload | Success: True | Details: Ingested %d files successfully (%s), %d files failed (%s)",
            username, role, len(success_files), str(success_files), len(failed_files), str(failed_files)
        )
        return UploadResponse(uploaded=uploaded_statuses, failed=failed_statuses)

    except HTTPException as he:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /admin/upload | Success: False | Details: Validation failed: %s",
            username, role, he.detail
        )
        raise he
    except Exception as e:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /admin/upload | Success: False | Details: Unexpected error: %s",
            username, role, str(e)
        )
        raise e

@router.get("/documents", response_model=List[DocumentMetadata])
def list_documents(current_user: dict = Depends(require_admin)):
    """List all ingested documents with metadata."""
    username = current_user["username"]
    role = current_user["role"]
    logger.info("Listing all ingested documents.")
    try:
        docs = db_service.get_all_documents()
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: GET /admin/documents | Success: True | Details: Listed %d documents",
            username, role, len(docs)
        )
        return [
            DocumentMetadata(
                id=doc["id"],
                filename=doc["filename"],
                upload_date=doc["upload_date"],
                chunk_count=doc["chunk_count"],
                status=doc["status"]
            )
            for doc in docs
        ]
    except Exception as e:
        logger.error("Failed to list documents: %s", e)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: GET /admin/documents | Success: False | Details: %s",
            username, role, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal database error: {str(e)}"
        )

@router.delete("/documents/{document_id}", response_model=DeleteResponse)
def delete_document(document_id: str, current_user: dict = Depends(require_admin)):
    """Remove a document and all its chunks/embeddings from the database."""
    username = current_user["username"]
    role = current_user["role"]
    logger.info("Request to delete document ID: %s", document_id)
    try:
        success = db_service.delete_document(document_id)
        if not success:
            audit_logger.info(
                "User: %s | Role: %s | Endpoint: DELETE /admin/documents/%s | Success: False | Details: Document not found",
                username, role, document_id
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document with ID '{document_id}' not found."
            )
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: DELETE /admin/documents/%s | Success: True | Details: Document deleted",
            username, role, document_id
        )
        return DeleteResponse(
            status="success",
            message=f"Document '{document_id}' and all its vector chunks were successfully deleted."
        )
    except HTTPException as he:
        raise he
    except Exception as e:
        logger.error("Failed to delete document %s: %s", document_id, e)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: DELETE /admin/documents/%s | Success: False | Details: %s",
            username, role, document_id, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal error during deletion: {str(e)}"
        )

@router.get("/debug-config")
def debug_config(current_user: dict = Depends(require_admin)):
    from app.config import MOCK_EMBEDDINGS, GEMINI_API_KEY
    from app.services.embedding_service import embedding_service
    from app.services.llm_service import llm_service
    
    key_length = len(GEMINI_API_KEY) if GEMINI_API_KEY else 0
    key_prefix = GEMINI_API_KEY[:6] if key_length > 6 else ""
    key_suffix = GEMINI_API_KEY[-4:] if key_length > 4 else ""
    
    return {
        "mock_embeddings": MOCK_EMBEDDINGS,
        "api_key_length": key_length,
        "api_key_prefix": key_prefix,
        "api_key_suffix": key_suffix,
        "embedding_service_configured": embedding_service.client_configured,
        "llm_service_configured": llm_service.client_configured
    }
