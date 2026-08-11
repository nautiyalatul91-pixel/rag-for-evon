import time
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse, FileResponse
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import logger, HOST, PORT, ALLOWED_ORIGINS
from app.routes import admin, chat, auth

app = FastAPI(
    title="RAG Chatbot Backend API",
    description="Phase 1 Ingestion Pipeline, Phase 2 Conversational RAG Query System, & Phase 3 Security controls",
    version="3.0.0"
)

# Enable CORS with restricted origin policy
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routes
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(chat.router)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Middleware to log request duration and status."""
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    logger.info(
        "Request: %s %s | Status: %s | Duration: %.4fs",
        request.method, request.url.path, response.status_code, duration
    )
    return response

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Custom handler for HTTP Exceptions."""
    logger.warning("HTTP Exception on %s: %s (Status: %d)", request.url.path, exc.detail, exc.status_code)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Custom handler for validation errors (e.g., missing parameters, invalid formats)."""
    errors = []
    for err in exc.errors():
        loc = " -> ".join(str(l) for l in err.get("loc", []))
        msg = err.get("msg", "Validation error")
        errors.append(f"{loc}: {msg}")
    
    error_msg = "; ".join(errors)
    logger.warning("Request validation failed on %s: %s", request.url.path, error_msg)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error": f"Validation failed: {error_msg}"}
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Custom handler to catch unhandled errors and prevent leaking raw stack traces to users."""
    logger.error("Unhandled exception on %s: %s", request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "An unexpected server error occurred. Please contact the administrator."}
    )

@app.on_event("startup")
def startup_event():
    from app.services.embedding_service import embedding_service
    from app.services.llm_service import llm_service
    from app.config import MOCK_EMBEDDINGS, GEMINI_API_KEY
    
    logger.info("========================================")
    logger.info("APPLICATION STARTUP CONFIGURATION CHECK")
    logger.info("MOCK_EMBEDDINGS: %s", MOCK_EMBEDDINGS)
    
    key_length = len(GEMINI_API_KEY) if GEMINI_API_KEY else 0
    key_prefix = GEMINI_API_KEY[:6] if key_length > 6 else ""
    key_suffix = GEMINI_API_KEY[-4:] if key_length > 4 else ""
    logger.info("GEMINI_API_KEY Length: %d (Starts with: '%s', Ends with: '%s')", key_length, key_prefix, key_suffix)
    
    logger.info("EmbeddingService Client Configured: %s", embedding_service.client_configured)
    logger.info("LLMService Client Configured: %s", llm_service.client_configured)
    logger.info("========================================")

@app.get("/")
def read_root():
    """Serve the frontend single-page application."""
    return FileResponse("static/index.html")

if __name__ == "__main__":
    import uvicorn
    logger.info("Starting Uvicorn server on %s:%d", HOST, PORT)
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)
