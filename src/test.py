from dotenv import load_dotenv
load_dotenv()

from src.adapters.vector_store import VectorStore

store = VectorStore()
print(store.list_sources())