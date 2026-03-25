"""
YellowPad API Gateway - Sample Microservice
This simulates the main API gateway that coordinates document ingestion,
connects to PostgreSQL (with pgvector), Redis for caching, and MinIO for
object storage.
"""
import os
import json
import logging
from contextlib import asynccontextmanager

import psycopg2
import redis
import boto3
from botocore.client import Config
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("api-gateway")

# --- Configuration from environment variables ---
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "yellowpad")
DB_USER = os.getenv("DB_USER", "yellowpad")
DB_PASSWORD = os.getenv("DB_PASSWORD", "yellowpad")

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "http://localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "documents")

DOCUMENT_PROCESSOR_URL = os.getenv("DOCUMENT_PROCESSOR_URL", "http://document-processor:8001")


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT,
        dbname=DB_NAME, user=DB_USER, password=DB_PASSWORD,
    )


def get_redis_client():
    return redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database schema and MinIO bucket on startup."""
    logger.info("Initializing API Gateway...")

    # Init DB
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id SERIAL PRIMARY KEY,
                filename TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                content_hash TEXT,
                embedding vector(384),
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        conn.commit()
        cur.close()
        conn.close()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.warning(f"Database not yet available: {e}")

    # Init MinIO bucket
    try:
        s3 = get_s3_client()
        try:
            s3.head_bucket(Bucket=MINIO_BUCKET)
        except Exception:
            s3.create_bucket(Bucket=MINIO_BUCKET)
            logger.info(f"Created bucket: {MINIO_BUCKET}")
    except Exception as e:
        logger.warning(f"MinIO not yet available: {e}")

    yield


app = FastAPI(title="YellowPad API Gateway", version="1.0.0", lifespan=lifespan)


class DocumentUpload(BaseModel):
    filename: str
    content: str  # base64 in production, plain text for this challenge


@app.get("/healthz")
def health_check():
    """Health check endpoint for Kubernetes liveness/readiness probes."""
    checks = {"api": "ok"}

    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1")
        cur.close()
        conn.close()
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {str(e)}"

    try:
        r = get_redis_client()
        r.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {str(e)}"

    try:
        s3 = get_s3_client()
        s3.list_buckets()
        checks["minio"] = "ok"
    except Exception as e:
        checks["minio"] = f"error: {str(e)}"

    all_ok = all(v == "ok" for v in checks.values())
    if not all_ok:
        raise HTTPException(status_code=503, detail=checks)
    return checks


@app.get("/")
def root():
    return {
        "service": "yellowpad-api-gateway",
        "version": "1.0.0",
        "status": "running",
    }


@app.post("/documents")
def upload_document(doc: DocumentUpload):
    """Upload a document for processing."""
    try:
        # Store metadata in PostgreSQL
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO documents (filename, status) VALUES (%s, %s) RETURNING id",
            (doc.filename, "pending"),
        )
        doc_id = cur.fetchone()[0]
        conn.commit()
        cur.close()
        conn.close()

        # Store document in MinIO
        s3 = get_s3_client()
        s3.put_object(
            Bucket=MINIO_BUCKET,
            Key=f"{doc_id}/{doc.filename}",
            Body=doc.content.encode(),
        )

        # Cache status in Redis
        r = get_redis_client()
        r.set(f"doc:{doc_id}:status", "pending", ex=3600)

        return {"id": doc_id, "filename": doc.filename, "status": "pending"}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/documents/{doc_id}")
def get_document(doc_id: int):
    """Get document status, checking Redis cache first."""
    r = get_redis_client()
    cached = r.get(f"doc:{doc_id}:status")
    if cached:
        return {"id": doc_id, "status": cached, "source": "cache"}

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id, filename, status, created_at FROM documents WHERE id = %s", (doc_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        raise HTTPException(status_code=404, detail="Document not found")

    return {
        "id": row[0],
        "filename": row[1],
        "status": row[2],
        "created_at": str(row[3]),
        "source": "database",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
