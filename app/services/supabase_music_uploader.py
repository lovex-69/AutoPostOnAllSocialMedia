"""
Supabase music uploader service.

Uploads royalty-free music files to the configured Supabase Storage bucket.
"""

from pathlib import Path

import requests

from app.config import settings


class SupabaseMusicUploadError(Exception):
    """Raised when a music upload to Supabase fails."""


_AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"}


def _get_base_config() -> tuple[str, str, str]:
    """Return (base_url, api_key, bucket) for storage operations."""
    base_url = (settings.SUPABASE_URL or "").strip()
    raw_key = settings.SUPABASE_SERVICE_ROLE_KEY or settings.SUPABASE_ANON_KEY
    api_key = (raw_key or "").strip()
    bucket = (settings.SUPABASE_MUSIC_BUCKET or "music").strip()

    if not base_url or not api_key:
        raise SupabaseMusicUploadError(
            "Supabase is not configured. Set SUPABASE_URL and a key "
            "(SUPABASE_SERVICE_ROLE_KEY preferred, or SUPABASE_ANON_KEY)."
        )

    return base_url, api_key, bucket


def upload_music_to_supabase(
    *,
    file_name: str,
    file_bytes: bytes,
    content_type: str,
    folder: str | None = None,
    upsert: bool = False,
) -> dict:
    """Upload one audio file to Supabase Storage and return object metadata."""
    base_url, api_key, bucket = _get_base_config()

    clean_name = Path(file_name).name
    clean_folder = (folder or "").strip("/")
    object_path = f"{clean_folder}/{clean_name}" if clean_folder else clean_name

    upload_url = f"{base_url}/storage/v1/object/{bucket}/{object_path}"
    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": content_type or "application/octet-stream",
        "x-upsert": "true" if upsert else "false",
    }

    response = requests.post(upload_url, headers=headers, data=file_bytes, timeout=90)

    if not response.ok:
        message = (
            f"Supabase upload failed ({response.status_code}): "
            f"{response.text[:300]}"
        )
        raise SupabaseMusicUploadError(message)

    public_url = f"{base_url}/storage/v1/object/public/{bucket}/{object_path}"

    return {
        "bucket": bucket,
        "path": object_path,
        "size": len(file_bytes),
        "public_url": public_url,
    }


def list_music_in_supabase(*, folder: str | None = None, limit: int = 200) -> list[dict]:
    """List audio files in the Supabase music bucket."""
    base_url, api_key, bucket = _get_base_config()

    clean_folder = (folder or "").strip("/")
    list_url = f"{base_url}/storage/v1/object/list/{bucket}"

    response = requests.post(
        list_url,
        json={"prefix": clean_folder, "limit": limit, "offset": 0},
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
        },
        timeout=30,
    )

    if not response.ok:
        message = (
            f"Supabase list failed ({response.status_code}): "
            f"{response.text[:300]}"
        )
        raise SupabaseMusicUploadError(message)

    objects = response.json() if isinstance(response.json(), list) else []
    items: list[dict] = []

    for item in objects:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        if Path(name).suffix.lower() not in _AUDIO_EXTENSIONS:
            continue
        object_path = f"{clean_folder}/{name}" if clean_folder else name
        items.append(
            {
                "name": name,
                "path": object_path,
                "size": item.get("metadata", {}).get("size"),
                "updated_at": item.get("updated_at"),
                "public_url": f"{base_url}/storage/v1/object/public/{bucket}/{object_path}",
            }
        )

    items.sort(key=lambda obj: (obj.get("name") or "").lower())
    return items


def delete_music_from_supabase(*, object_path: str) -> dict:
    """Delete one file from the Supabase music bucket by object path."""
    base_url, api_key, bucket = _get_base_config()
    clean_path = object_path.strip().strip("/")
    if not clean_path:
        raise SupabaseMusicUploadError("Missing object path to delete.")

    delete_url = f"{base_url}/storage/v1/object/{bucket}"
    response = requests.delete(
        delete_url,
        json={"prefixes": [clean_path]},
        headers={
            "apikey": api_key,
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )

    if not response.ok:
        message = (
            f"Supabase delete failed ({response.status_code}): "
            f"{response.text[:300]}"
        )
        raise SupabaseMusicUploadError(message)

    return {"bucket": bucket, "path": clean_path, "deleted": True}
