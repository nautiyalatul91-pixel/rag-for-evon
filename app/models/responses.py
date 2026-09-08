from pydantic import BaseModel
from typing import List, Optional

class DocumentMetadata(BaseModel):
    id: str
    filename: str
    upload_date: str
    chunk_count: int
    status: str
    collection_name: str
    allowed_roles: List[str] = []
    uploader_username: Optional[str] = "Unknown"
    uploader_role: Optional[str] = "Unknown"

class UploadStatus(BaseModel):
    filename: str
    status: str
    document_id: Optional[str] = None
    chunks: Optional[int] = None
    allowed_roles: Optional[List[str]] = None
    uploader_username: Optional[str] = None
    uploader_role: Optional[str] = None
    error: Optional[str] = None

class UploadResponse(BaseModel):
    uploaded: List[UploadStatus]
    failed: List[UploadStatus]
    
class DeleteResponse(BaseModel):
    status: str
    message: str
