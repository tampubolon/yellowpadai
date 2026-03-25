"""
YellowPad Document Processor - Sample Worker Microservice
This simulates a background worker that processes documents from the queue,
extracts text, and stores results. In production this would consume from
Pub/Sub (or NATS/Redis Streams in on-prem).
"""
import os
import time
import hashlib
import logging
from contextlib import asynccontextmanager

import psycopg2
import redis
import boto3
from botocore.client import Config
from fastapi import FastAPI, HTTPException

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("document-processor")

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
    logger.info("Document Processor starting up...")
    yield


app = FastAPI(title="YellowPad Document Processor", version="1.0.0", lifespan=lifespan)


@app.get("/healthz")
def health_check():
    """Health check for K8s probes."""
    return {"status": "ok", "service": "document-processor"}


@app.get("/")
def root():
    return {
        "service": "yellowpad-document-processor",
        "version": "1.0.0",
        "status": "running",
    }


@app.post("/process/{doc_id}")
def process_document(doc_id: int):
    """
    Simulate document processing: fetch from MinIO, compute hash,
    update status in PostgreSQL, invalidate Redis cache.
    """
    try:
        # Fetch document metadata
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT filename FROM documents WHERE id = %s", (doc_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Document not found")
        filename = row[0]

        # Fetch from MinIO
        s3 = get_s3_client()
        obj = s3.get_object(Bucket=MINIO_BUCKET, Key=f"{doc_id}/{filename}")
        content = obj["Body"].read()

        # Simulate processing (hash the content)
        content_hash = hashlib.sha256(content).hexdigest()
        logger.info(f"Processed document {doc_id}: hash={content_hash[:16]}...")

        # Update database
        cur.execute(
            "UPDATE documents SET status = %s, content_hash = %s WHERE id = %s",
            ("processed", content_hash, doc_id),
        )
        conn.commit()
        cur.close()
        conn.close()

        # Update Redis cache
        r = get_redis_client()
        r.set(f"doc:{doc_id}:status", "processed", ex=3600)

        return {
            "id": doc_id,
            "status": "processed",
            "content_hash": content_hash,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
