from collections.abc import AsyncIterator

from bson import ObjectId
from gridfs.errors import NoFile
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from . import db


async def store_upload(
    filename: str,
    content: bytes,
    content_type: str | None,
) -> str:
    if db.database is None:
        raise RuntimeError("Database is not initialized")

    bucket = AsyncIOMotorGridFSBucket(db.database)
    file_id = await bucket.upload_from_stream(
        filename,
        content,
        metadata={"contentType": content_type or "application/octet-stream"},
    )
    return str(file_id)


async def stream_upload(file_id: str) -> tuple[AsyncIterator[bytes], str, int]:
    if db.database is None:
        raise RuntimeError("Database is not initialized")

    try:
        object_id = ObjectId(file_id)
    except Exception as exc:
        raise ValueError("Invalid file id") from exc

    bucket = AsyncIOMotorGridFSBucket(db.database)
    try:
        grid_file = await bucket.open_download_stream(object_id)
    except NoFile as exc:
        raise FileNotFoundError(file_id) from exc

    async def chunks() -> AsyncIterator[bytes]:
        try:
            while chunk := await grid_file.readchunk():
                yield chunk
        finally:
            await grid_file.close()

    content_type = str(
        (grid_file.metadata or {}).get(
            "contentType",
            "application/octet-stream",
        )
    )
    return chunks(), content_type, grid_file.length
