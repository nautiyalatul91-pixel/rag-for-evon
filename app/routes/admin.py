import os
import json
import uuid
import shutil
import tempfile
import hashlib
from typing import List, Optional
from fastapi import APIRouter, File, UploadFile, HTTPException, status, Depends, Form

from app.config import logger, audit_logger
from app.models.responses import UploadResponse, UploadStatus, DocumentMetadata, DeleteResponse
from app.models.role import RoleResponse, RoleCreateRequest, RoleUpdateRequest, RoleDeleteResponse
from app.services.db_service import db_service
from app.services.parser_service import parser_service
from app.services.chunking_service import chunking_service
from app.services.embedding_service import embedding_service
from app.services.auth_service import require_admin, require_upload_permission

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
def upload_documents(
    files: List[UploadFile] = File(...),
    collection: str = Form("company_knowledge_base_gemini_3072"),
    allowed_roles: Optional[str] = Form(None),
    current_user: dict = Depends(require_upload_permission)
):
    """
    Ingests one or more documents (PDF, DOCX, XLSX, TXT) synchronously.
    Validates file extension and size (< 20MB) early.
    Enforces role hierarchy access tagging:
      - Automatically grants access to all roles positioned at or above the uploader in the hierarchy (including uploader).
      - Allows selection of roles strictly below the uploader.
      - Strictly rejects requests attempting to manually select roles above or outside downline.
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested upload of %d files to collection '%s'.", username, role, len(files), collection)
    
    valid_collections = {"company_knowledge_base_gemini_3072", "evon_capabilities"}
    if collection not in valid_collections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid collection: '{collection}'. Allowed collections: {list(valid_collections)}."
        )
    ALLOWED_EXTENSIONS = {"pdf", "docx", "xlsx", "txt"}
    MAX_FILE_SIZE = 20 * 1024 * 1024  # 20MB

    try:
        # 1. Early Validation Phase (Extensions and Sizes)
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

        # 2. Hierarchy Access List Calculation & Security Validation
        all_roles = db_service.list_roles()
        current_role_record = db_service.get_role(role)
        if not current_role_record:
            for r in all_roles:
                if role.lower() in (r["role_id"].lower(), r["role_name"].lower()):
                    current_role_record = r
                    break
        if not current_role_record:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Uploader role not found in roles hierarchy."
            )

        uploader_pos = current_role_record["hierarchy_position"]
        uploader_canonical_role = current_role_record["role_id"]

        # Automatic access: All roles at or above the uploader
        auto_roles = [r["role_id"] for r in all_roles if r["hierarchy_position"] <= uploader_pos]

        # Valid selectable downline roles: strictly higher hierarchy_position numbers
        downline_roles_map = {r["role_id"]: r for r in all_roles if r["hierarchy_position"] > uploader_pos}

        selected_roles = []
        if allowed_roles:
            raw_str = allowed_roles.strip()
            if raw_str.startswith("[") and raw_str.endswith("]"):
                try:
                    parsed = json.loads(raw_str)
                    selected_roles = [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    selected_roles = [s.strip() for s in raw_str.strip("[]").split(",") if s.strip()]
            else:
                selected_roles = [s.strip() for s in raw_str.split(",") if s.strip()]

            for s_role in selected_roles:
                if s_role not in downline_roles_map:
                    logger.warning(
                        "Upload security violation: User '%s' (role: '%s', pos: %d) attempted to grant access to unauthorized role '%s'.",
                        username, role, uploader_pos, s_role
                    )
                    audit_logger.info(
                        "User: %s | Role: %s | Endpoint: POST /admin/upload | Success: False | Details: Attempted to grant access to unauthorized role '%s'",
                        username, role, s_role
                    )
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid access assignment: Role '{s_role}' cannot be manually selected. Only roles strictly below you in the hierarchy ({list(downline_roles_map.keys())}) can be selected."
                    )

        # Final computed access list
        final_access_list = list(dict.fromkeys(auto_roles + selected_roles))
        logger.info(
            "Upload access list calculated for user '%s' (role: '%s'): Automatic=%s | Selected downline=%s | Final access list=%s",
            username, uploader_canonical_role, auto_roles, selected_roles, final_access_list
        )

        uploaded_statuses = []
        failed_statuses = []

        # 3. Processing Phase (Synchronous)
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
                is_duplicate, duplicate_reason = db_service.check_duplicate(filename, content_hash, collection_name=collection)
                if is_duplicate:
                    logger.warning("Duplicate detected for file %s: %s", filename, duplicate_reason)
                    failed_statuses.append(UploadStatus(
                        filename=filename,
                        status="failure",
                        allowed_roles=final_access_list,
                        uploader_username=username,
                        uploader_role=uploader_canonical_role,
                        error=duplicate_reason
                    ))
                    continue

                # Create document record in SQLite (status: processing) with access tags
                db_service.create_document_record(
                    doc_id=doc_id,
                    filename=filename,
                    content_hash=content_hash,
                    collection_name=collection,
                    allowed_roles=final_access_list,
                    uploader_username=username,
                    uploader_role=uploader_canonical_role
                )

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

                # D. Store in ChromaDB with dual-storage access tags
                db_service.add_chunks_to_chroma(
                    doc_id=doc_id,
                    filename=filename,
                    content_hash=content_hash,
                    chunks=chunks,
                    embeddings=all_embeddings,
                    collection_name=collection,
                    allowed_roles=final_access_list,
                    uploader_username=username,
                    uploader_role=uploader_canonical_role
                )

                # E. Update status to completed
                db_service.update_document_status(doc_id, "completed", len(chunks))

                uploaded_statuses.append(UploadStatus(
                    filename=filename,
                    status="success",
                    document_id=doc_id,
                    chunks=len(chunks),
                    allowed_roles=final_access_list,
                    uploader_username=username,
                    uploader_role=uploader_canonical_role
                ))
                logger.info("Successfully ingested document: %s (ID: %s, roles: %s)", filename, doc_id, final_access_list)

            except Exception as e:
                logger.error("Failed to ingest document %s: %s", filename, e, exc_info=True)
                try:
                    db_service.update_document_status(doc_id, "failed", 0)
                except Exception as db_err:
                    logger.error("Failed to update status to failed for %s: %s", filename, db_err)

                failed_statuses.append(UploadStatus(
                    filename=filename,
                    status="failure",
                    allowed_roles=final_access_list,
                    uploader_username=username,
                    uploader_role=uploader_canonical_role,
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
            "User: %s | Role: %s | Endpoint: POST /admin/upload | Success: True | Details: Ingested %d files successfully (%s), %d files failed (%s) | Access: %s (auto: %s, selected: %s)",
            username, role, len(success_files), str(success_files), len(failed_files), str(failed_files), str(final_access_list), str(auto_roles), str(selected_roles)
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
def list_documents(collection: Optional[str] = None, current_user: dict = Depends(require_upload_permission)):
    """List all ingested documents with metadata, optionally filtered by collection."""
    username = current_user["username"]
    role = current_user["role"]
    logger.info("Listing all ingested documents. Filter collection: %s", collection)
    
    if collection:
        valid_collections = {"company_knowledge_base_gemini_3072", "evon_capabilities"}
        if collection not in valid_collections:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid collection filter: '{collection}'."
            )
            
    try:
        docs = db_service.get_all_documents(collection_name=collection)
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
                status=doc["status"],
                collection_name=doc.get("collection_name", "company_knowledge_base_gemini_3072"),
                allowed_roles=doc.get("allowed_roles") or [],
                uploader_username=doc.get("uploader_username") or "Unknown",
                uploader_role=doc.get("uploader_role") or "Unknown"
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
    import os
    from app.config import MOCK_EMBEDDINGS, GEMINI_API_KEY
    from app.services.embedding_service import embedding_service
    from app.services.llm_service import llm_service
    
    key_length = len(GEMINI_API_KEY) if GEMINI_API_KEY else 0
    key_prefix = GEMINI_API_KEY[:6] if key_length > 6 else ""
    key_suffix = GEMINI_API_KEY[-4:] if key_length > 4 else ""
    
    env_keys = list(os.environ.keys())
    
    return {
        "mock_embeddings": MOCK_EMBEDDINGS,
        "api_key_length": key_length,
        "api_key_prefix": key_prefix,
        "api_key_suffix": key_suffix,
        "embedding_service_configured": embedding_service.client_configured,
        "llm_service_configured": llm_service.client_configured,
        "env_keys": env_keys
    }

# ==========================================
# Phase 11 & 14: Dynamic Role Management Endpoints
# ==========================================

@router.get("/roles/below-me", response_model=List[RoleResponse])
def get_roles_below_me(current_user: dict = Depends(require_upload_permission)):
    """
    Returns roles with a higher hierarchy_position than the current user (valid downline options).
    Accessible to any user with upload permissions.
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested roles below them in the hierarchy.", username, role)
    try:
        roles_below = db_service.get_roles_below(role)
        return [RoleResponse(**r) for r in roles_below]
    except Exception as e:
        logger.error("Failed to list downline roles for '%s': %s", role, e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list downline roles: {str(e)}"
        )

@router.get("/roles", response_model=List[RoleResponse])
def get_roles(current_user: dict = Depends(require_admin)):
    """
    List all roles ordered by hierarchy_position ascending (admin only).
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested roles list.", username, role)
    try:
        roles = db_service.list_roles()
        return [RoleResponse(**r) for r in roles]
    except Exception as e:
        logger.error("Failed to list roles: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list roles: {str(e)}"
        )

@router.post("/roles", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
def create_role(
    payload: RoleCreateRequest,
    current_user: dict = Depends(require_admin)
):
    """
    Create a new role, positioning it and shifting lower roles down (admin only).
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested creation of role '%s'.", username, role, payload.role_name)
    try:
        new_role = db_service.create_role(
            role_name=payload.role_name,
            insert_below_role_id=payload.insert_below_role_id,
            can_upload=payload.can_upload,
            role_id=payload.role_id
        )
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /admin/roles | Success: True | Details: Created role '%s' (ID: %s, Position: %d)",
            username, role, new_role["role_name"], new_role["role_id"], new_role["hierarchy_position"]
        )
        return RoleResponse(**new_role)
    except ValueError as ve:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /admin/roles | Success: False | Details: %s",
            username, role, str(ve)
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        logger.error("Failed to create role: %s", e, exc_info=True)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: POST /admin/roles | Success: False | Details: Internal error: %s",
            username, role, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create role: {str(e)}"
        )

@router.patch("/roles/{role_id}", response_model=RoleResponse)
def update_role(
    role_id: str,
    payload: RoleUpdateRequest,
    current_user: dict = Depends(require_admin)
):
    """
    Update role name, upload permissions, or hierarchy position (admin only).
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested update of role '%s'.", username, role, role_id)
    try:
        updated = db_service.update_role(
            role_id=role_id,
            role_name=payload.role_name,
            can_upload=payload.can_upload,
            insert_below_role_id=payload.insert_below_role_id,
            hierarchy_position=payload.hierarchy_position
        )
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: PATCH /admin/roles/%s | Success: True | Details: Updated role '%s' (Position: %d)",
            username, role, role_id, updated["role_name"], updated["hierarchy_position"]
        )
        return RoleResponse(**updated)
    except ValueError as ve:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: PATCH /admin/roles/%s | Success: False | Details: %s",
            username, role, role_id, str(ve)
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        logger.error("Failed to update role '%s': %s", role_id, e, exc_info=True)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: PATCH /admin/roles/%s | Success: False | Details: Internal error: %s",
            username, role, role_id, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to update role: {str(e)}"
        )

@router.delete("/roles/{role_id}", response_model=RoleDeleteResponse)
def delete_role(
    role_id: str,
    current_user: dict = Depends(require_admin)
):
    """
    Delete a role if not protected and not assigned to any user (admin only).
    """
    username = current_user["username"]
    role = current_user["role"]
    logger.info("User '%s' (role: '%s') requested deletion of role '%s'.", username, role, role_id)
    try:
        db_service.delete_role(role_id=role_id)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: DELETE /admin/roles/%s | Success: True | Details: Role deleted",
            username, role, role_id
        )
        return RoleDeleteResponse(
            success=True,
            message=f"Role '{role_id}' was successfully deleted.",
            deleted_role_id=role_id
        )
    except ValueError as ve:
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: DELETE /admin/roles/%s | Success: False | Details: %s",
            username, role, role_id, str(ve)
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(ve)
        )
    except Exception as e:
        logger.error("Failed to delete role '%s': %s", role_id, e, exc_info=True)
        audit_logger.info(
            "User: %s | Role: %s | Endpoint: DELETE /admin/roles/%s | Success: False | Details: Internal error: %s",
            username, role, role_id, str(e)
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete role: {str(e)}"
        )
