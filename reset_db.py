import os
import shutil
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

def reset():
    print("=" * 60)
    print("RESETTING RAG INGESTION PIPELINE DATABASES")
    print("=" * 60)
    
    # 1. Clear ChromaDB
    chroma_path = DATA_DIR / "chroma_db"
    if chroma_path.exists():
        print(f"Removing ChromaDB folder at: {chroma_path}")
        try:
            shutil.rmtree(chroma_path)
            print("  [SUCCESS] ChromaDB folder removed.")
        except Exception as e:
            print(f"  [ERROR] Failed to remove ChromaDB: {e}")
    else:
        print("No ChromaDB folder found.")

    # 2. Clear SQLite Metadata
    sqlite_path = DATA_DIR / "metadata.db"
    if sqlite_path.exists():
        print(f"Removing SQLite database at: {sqlite_path}")
        try:
            os.remove(sqlite_path)
            print("  [SUCCESS] SQLite database file removed.")
        except Exception as e:
            print(f"  [ERROR] Failed to remove SQLite database: {e}")
            print("\n  >> IMPORTANT: If the file is locked, make sure you stop your running FastAPI server first.")
    else:
        print("No SQLite database found.")
        
    print("\nReset complete. Databases will be initialized fresh on your next upload.")
    print("=" * 60)

if __name__ == "__main__":
    reset()
