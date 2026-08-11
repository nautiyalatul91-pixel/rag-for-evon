import sqlite3
import chromadb
from pathlib import Path

# Paths to databases
ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

def inspect_env(sqlite_name, chroma_name, title):
    sqlite_path = DATA_DIR / sqlite_name
    chroma_path = DATA_DIR / chroma_name
    
    print("\n" + "=" * 60)
    print(f"DATABASE ENVIRONMENT: {title}")
    print("=" * 60)
    
    # --- SQLite Ingestion Records ---
    print("\n--- SQLite Document Records ---")
    if not sqlite_path.exists():
        print(f"  [No SQLite file found at {sqlite_path}]")
    else:
        try:
            conn = sqlite3.connect(sqlite_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT * FROM documents")
            rows = cursor.fetchall()
            
            if not rows:
                print("  (SQLite database is empty - no documents ingested)")
            else:
                for idx, row in enumerate(rows):
                    print(f"  Document #{idx + 1}:")
                    print(f"    ID:          {row['id']}")
                    print(f"    Filename:    {row['filename']}")
                    print(f"    Status:      {row['status']}")
                    print(f"    Chunks:      {row['chunk_count']}")
                    print(f"    Hash:        {row['content_hash']}")
                    print(f"    Upload Date: {row['upload_date']}")
                    print("    " + "-" * 40)
            conn.close()
        except Exception as e:
            print(f"  Error reading SQLite: {e}")
            
    # --- ChromaDB Vector Chunks ---
    print("\n--- ChromaDB Vector Chunks ---")
    if not chroma_path.exists():
        print(f"  [No ChromaDB directory found at {chroma_path}]")
    else:
        try:
            chroma_client = chromadb.PersistentClient(path=str(chroma_path))
            collections = chroma_client.list_collections()
            collection_names = [col.name for col in collections]
            print(f"  Available collections: {collection_names}")
            
            if "company_knowledge_base" not in collection_names:
                print("  (Collection 'company_knowledge_base' is not created yet)")
            else:
                collection = chroma_client.get_collection(name="company_knowledge_base")
                count = collection.count()
                print(f"  Total chunks in vector database: {count}")
                
                if count > 0:
                    sample = collection.get(limit=2, include=["documents", "metadatas", "embeddings"])
                    for idx in range(len(sample["ids"])):
                        print(f"\n    Vector Chunk #{idx + 1}:")
                        print(f"      Chunk ID:    {sample['ids'][idx]}")
                        print(f"      Source File: {sample['metadatas'][idx].get('source_filename')}")
                        print(f"      Page/Sheet:  {sample['metadatas'][idx].get('page_number')}")
                        print(f"      Chunk Index: {sample['metadatas'][idx].get('chunk_index')}")
                        print(f"      Timestamp:   {sample['metadatas'][idx].get('upload_timestamp')}")
                        
                        snippet = sample['documents'][idx].replace('\n', ' ')
                        if len(snippet) > 100:
                            snippet = snippet[:100] + "..."
                        print(f"      Text:        \"{snippet}\"")
                        
                        vector = sample['embeddings'][idx]
                        vector_snippet = [round(val, 4) for val in vector[:4]]
                        print(f"      Embedding:   {vector_snippet}... (dimension: {len(vector)})")
                        print("      " + "-" * 40)
        except Exception as e:
            print(f"  Error reading ChromaDB: {e}")

if __name__ == "__main__":
    if not DATA_DIR.exists():
        print(f"No data directory found at {DATA_DIR}. Run tests or start the server first.")
    else:
        # Inspect Production Database env
        inspect_env("metadata.db", "chroma_db", "Production Storage (metadata.db / chroma_db)")
        # Inspect Test Database env
        inspect_env("test_metadata.db", "test_chroma_db", "Test Validation Storage (test_metadata.db / test_chroma_db)")
