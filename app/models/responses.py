from pydantic import BaseModel
from typing import List, Optional

class DocumentMetadata(BaseModel):
    id: str
    filename: str
    upload_date: str
    chunk_count: int
    status: str

class UploadStatus(BaseModel):
    filename: str
    status: str
    document_id: Optional[str] = None
    chunks: Optional[int] = None
    error: Optional[str] = None

class UploadResponse(BaseModel):
    uploaded: List[UploadStatus]
    failed: List[UploadStatus]
    
class DeleteResponse(BaseModel):
    status: str
    message: str
