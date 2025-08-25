import os
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB  = os.getenv("MONGO_DB", "hoaxbuster")
MONGO_COLL= os.getenv("MONGO_COLL", "articles")

_client: AsyncIOMotorClient | None = None

async def connect() -> None:
    global _client
    if _client is None:
        try:
            _client = AsyncIOMotorClient(MONGO_URI, uuidRepresentation="standard")
            await _client.admin.command('ping')
            print(f"[mongo] connected to database: {MONGO_DB}")
        except Exception as e:
            print(f"[mongo] connection failed: {e}")
            _client = None
            raise

async def close() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None

def coll():
    if _client is None:
        raise RuntimeError("Mongo client not initialized")
    return _client[MONGO_DB][MONGO_COLL]

async def ensure_indexes():
    c = coll()
    await c.create_index([("url", 1)], unique=True, sparse=True, name="uniq_url_sparse")
    await c.create_index([("source", 1), ("published_at", -1)], name="src_pub_desc")
    await c.create_index([("published_at", -1)], name="pub_desc")
    await c.create_index([("created_at", -1)], name="created_desc")
    await c.create_index([("label", 1), ("published_at", -1), ("created_at", -1)], name="hoax_list_idx")
    await c.create_index([("verdict", 1)], name="verdict_idx")

