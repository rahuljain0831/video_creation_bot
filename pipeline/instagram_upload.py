"""Instagram Reels upload orchestration and Meta Graph API integration."""
import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx

from pipeline import instagram_auth, instagram_quota

logger = logging.getLogger(__name__)


async def upload_reel_to_instagram(
    video_id: int,
    file_path: str,
    caption: str,
    hashtags: list[str],
    account: dict,
    creds: dict,
    conn: sqlite3.Connection,
    cfg,
) -> dict:
    """
    Upload a video to Instagram Reels.

    Args:
        video_id: Video ID from database
        file_path: Path to MP4 video file
        caption: Caption text for the post
        hashtags: List of hashtag strings
        account: Account config dict
        creds: Credentials dict with access_token
        conn: SQLite connection
        cfg: Config object

    Returns:
        {
            "success": bool,
            "media_id": str | None,
            "container_id": str | None,
            "post_url": str | None,
            "error_msg": str | None,
            "error_code": int | None,
            "meta_error_type": str | None
        }
    """
    account_id = account["account_id"]
    ig_business_id = account.get("ig_business_id") or creds.get("ig_business_id")

    logger.info(f"Starting Instagram upload for video {video_id} (account: {account_id})")

    if not instagram_quota.can_post_to_instagram(account_id, conn, cfg):
        error_msg = "Daily upload limit reached (25 posts/day)"
        logger.warning(error_msg)
        instagram_quota.log_instagram_post(
            video_id, account_id, "permanently_failed", None, 429, conn,
            ig_business_id=ig_business_id, error_msg=error_msg
        )
        return {
            "success": False,
            "media_id": None,
            "container_id": None,
            "post_url": None,
            "error_msg": error_msg,
            "error_code": 429,
            "meta_error_type": "RATE_LIMIT",
        }

    try:
        if not Path(file_path).exists():
            raise FileNotFoundError(f"Video file not found: {file_path}")

        caption_text = f"{caption}\n\n{' '.join(hashtags)}" if hashtags else caption

        ig_post_id = instagram_quota.log_instagram_post(
            video_id, account_id, "uploading", None, None, conn,
            ig_business_id=ig_business_id,
            caption_text=caption_text,
            hashtags=" ".join(hashtags) if hashtags else None,
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            container_id, container_resp = await create_media_container(
                ig_business_id, file_path, caption_text, creds["access_token"], client
            )

            if not container_id:
                error = container_resp.get("error", {})
                error_msg = error.get("message", "Unknown error creating container")
                error_code = error.get("code", 400)
                meta_error_type = error.get("type", "UNKNOWN")

                instagram_quota.mark_permanently_failed(
                    ig_post_id, conn, error_code, error_msg
                )
                return {
                    "success": False,
                    "media_id": None,
                    "container_id": None,
                    "post_url": None,
                    "error_msg": error_msg,
                    "error_code": error_code,
                    "meta_error_type": meta_error_type,
                }

            conn.execute(
                "UPDATE instagram_posts SET container_id = ? WHERE id = ?",
                (container_id, ig_post_id),
            )
            conn.commit()

            conn.execute(
                "UPDATE instagram_posts SET upload_status = 'processing' WHERE id = ?",
                (ig_post_id,),
            )
            conn.commit()

            media_id, publish_resp = await publish_media_container(
                ig_business_id, container_id, creds["access_token"], client
            )

            if not media_id:
                error = publish_resp.get("error", {})
                error_msg = error.get("message", "Unknown error publishing container")
                error_code = error.get("code", 400)
                meta_error_type = error.get("type", "UNKNOWN")

                instagram_quota.mark_permanently_failed(
                    ig_post_id, conn, error_code, error_msg
                )
                return {
                    "success": False,
                    "media_id": None,
                    "container_id": container_id,
                    "post_url": None,
                    "error_msg": error_msg,
                    "error_code": error_code,
                    "meta_error_type": meta_error_type,
                }

            post_url = f"https://instagram.com/reel/{media_id}"
            instagram_quota.mark_published(ig_post_id, conn, media_id, post_url)

            logger.info(
                f"Successfully published video {video_id} to Instagram (media_id: {media_id})"
            )

            return {
                "success": True,
                "media_id": media_id,
                "container_id": container_id,
                "post_url": post_url,
                "error_msg": None,
                "error_code": None,
                "meta_error_type": None,
            }

    except FileNotFoundError as e:
        error_msg = str(e)
        instagram_quota.mark_permanently_failed(
            instagram_quota.log_instagram_post(
                video_id, account_id, "permanently_failed", None, 400, conn,
                ig_business_id=ig_business_id, error_msg=error_msg
            ),
            conn, 400, error_msg
        )
        return {
            "success": False,
            "media_id": None,
            "container_id": None,
            "post_url": None,
            "error_msg": error_msg,
            "error_code": 400,
            "meta_error_type": "FILE_NOT_FOUND",
        }
    except asyncio.TimeoutError as e:
        error_msg = f"Request timeout: {str(e)}"
        logger.error(error_msg)
        ig_post_id = instagram_quota.log_instagram_post(
            video_id, account_id, "failed", None, 408, conn,
            ig_business_id=ig_business_id, error_msg=error_msg
        )
        return {
            "success": False,
            "media_id": None,
            "container_id": None,
            "post_url": None,
            "error_msg": error_msg,
            "error_code": 408,
            "meta_error_type": "TIMEOUT",
        }
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.exception(error_msg)
        ig_post_id = instagram_quota.log_instagram_post(
            video_id, account_id, "failed", None, 500, conn,
            ig_business_id=ig_business_id, error_msg=error_msg
        )
        return {
            "success": False,
            "media_id": None,
            "container_id": None,
            "post_url": None,
            "error_msg": error_msg,
            "error_code": 500,
            "meta_error_type": "INTERNAL_ERROR",
        }


async def create_media_container(
    ig_business_id: str,
    file_path: str,
    caption: str,
    access_token: str,
    client: httpx.AsyncClient,
) -> tuple[str | None, dict]:
    """
    Create media container on Instagram (upload video).

    Args:
        ig_business_id: Instagram business account ID
        file_path: Path to MP4 video file
        caption: Caption text
        access_token: Meta API access token
        client: httpx AsyncClient

    Returns:
        (container_id, response_dict) or (None, error_dict)
    """
    url = f"https://graph.instagram.com/v21.0/{ig_business_id}/media"

    try:
        with open(file_path, "rb") as f:
            files = {"video": f}
            data = {
                "media_type": "REELS",
                "caption": caption,
                "access_token": access_token,
            }
            resp = await client.post(url, data=data, files=files)
            resp.raise_for_status()
            result = resp.json()
            return result.get("id"), result
    except httpx.HTTPStatusError as e:
        try:
            error_data = e.response.json()
        except Exception:
            error_data = {"error": {"message": str(e), "code": e.response.status_code}}
        logger.error(f"Create container failed: {error_data}")
        return None, error_data
    except Exception as e:
        logger.exception(f"Create container error: {str(e)}")
        return None, {"error": {"message": str(e), "code": 500}}


async def publish_media_container(
    ig_business_id: str,
    container_id: str,
    access_token: str,
    client: httpx.AsyncClient,
) -> tuple[str | None, dict]:
    """
    Publish media container (go live on Instagram).

    Args:
        ig_business_id: Instagram business account ID
        container_id: Container ID from create_media_container
        access_token: Meta API access token
        client: httpx AsyncClient

    Returns:
        (media_id, response_dict) or (None, error_dict)
    """
    url = f"https://graph.instagram.com/v21.0/{ig_business_id}/media_publish"

    try:
        data = {
            "creation_id": container_id,
            "access_token": access_token,
        }
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        result = resp.json()
        return result.get("media_id") or result.get("id"), result
    except httpx.HTTPStatusError as e:
        try:
            error_data = e.response.json()
        except Exception:
            error_data = {"error": {"message": str(e), "code": e.response.status_code}}
        logger.error(f"Publish container failed: {error_data}")
        return None, error_data
    except Exception as e:
        logger.exception(f"Publish container error: {str(e)}")
        return None, {"error": {"message": str(e), "code": 500}}


def calculate_retry_delay(retry_count: int) -> int:
    """
    Calculate exponential backoff delay.

    Args:
        retry_count: Number of retries attempted (0-indexed)

    Returns:
        Seconds to wait before next retry
    """
    delays = [60, 300, 1800]
    return delays[min(retry_count, len(delays) - 1)]


async def retry_failed_upload(
    instagram_post_id: int,
    conn: sqlite3.Connection,
    cfg,
) -> dict:
    """
    Retry a failed Instagram upload.

    Args:
        instagram_post_id: ID from instagram_posts table
        conn: SQLite connection
        cfg: Config object

    Returns:
        Result dict from upload_reel_to_instagram
    """
    cursor = conn.execute(
        "SELECT * FROM instagram_posts WHERE id = ?", (instagram_post_id,)
    )
    row = cursor.fetchone()
    if not row:
        return {"success": False, "error_msg": "Instagram post not found"}

    ig_post = dict(row)

    if ig_post["retry_count"] >= ig_post["max_retries"]:
        return {
            "success": False,
            "error_msg": f"Max retries ({ig_post['max_retries']}) exceeded",
        }

    video_id = ig_post["video_id"]
    account_id = ig_post["account_id"]

    cursor = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,))
    video = cursor.fetchone()
    if not video:
        return {"success": False, "error_msg": "Video not found"}

    video = dict(video)

    try:
        account_dict, creds = instagram_auth.load_instagram_account(account_id, cfg)
    except (FileNotFoundError, ValueError) as e:
        return {"success": False, "error_msg": str(e)}

    result = await upload_reel_to_instagram(
        video_id,
        video["file_path"],
        ig_post["caption_text"],
        (ig_post["hashtags"] or "").split(),
        account_dict,
        creds,
        conn,
        cfg,
    )

    if not result["success"]:
        instagram_quota.increment_retry_count(
            instagram_post_id,
            conn,
            result["error_code"],
            result["error_msg"],
        )

    return result
