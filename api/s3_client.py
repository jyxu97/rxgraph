"""
S3 helper — image upload and retrieval.

Buckets (from design doc):
  rxgraph-uploads/   — prescription label images from users
  rxgraph-raw-data/  — raw OpenFDA API responses (written by ingest script)

Why boto3 and not a higher-level wrapper?
boto3 is the AWS standard library. The operations here are simple
(put_object, get_object) — no abstraction layer needed.

Credentials: boto3 reads from environment variables automatically:
  AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION
Do not hardcode credentials.
"""

import os
import uuid
from datetime import datetime

import boto3
from botocore.exceptions import ClientError

UPLOADS_BUCKET = os.environ.get("S3_UPLOADS_BUCKET", "rxgraph-uploads")
RAW_DATA_BUCKET = os.environ.get("S3_RAW_DATA_BUCKET", "rxgraph-raw-data")


def _get_client():
    return boto3.client("s3")


def upload_image(file_bytes: bytes, original_filename: str, session_id: str) -> str:
    """
    Upload a prescription label image to S3.

    Key format: {session_id}/{timestamp}_{uuid}_{filename}
    The UUID prevents collisions if the same user uploads the same filename twice.

    Returns the S3 key (not a full URL — the agent stores the key and fetches
    the image when needed, rather than relying on a pre-signed URL that expires).
    """
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    unique_id = uuid.uuid4().hex[:8]
    safe_filename = original_filename.replace(" ", "_")
    key = f"{session_id}/{timestamp}_{unique_id}_{safe_filename}"

    client = _get_client()
    client.put_object(
        Bucket=UPLOADS_BUCKET,
        Key=key,
        Body=file_bytes,
        ContentType=_guess_content_type(original_filename),
    )
    return key


def get_image(s3_key: str) -> bytes:
    """
    Download an image from S3 by key.
    Called by Node 1 (entity_extraction) when image_s3_key is present in state.

    Raises ClientError if the key doesn't exist or access is denied.
    """
    client = _get_client()
    try:
        response = client.get_object(Bucket=UPLOADS_BUCKET, Key=s3_key)
        return response["Body"].read()
    except ClientError as e:
        raise RuntimeError(f"S3 fetch failed for key '{s3_key}': {e}") from e


def _guess_content_type(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1]
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "heic": "image/heic",
        "pdf": "application/pdf",
    }.get(ext, "application/octet-stream")
