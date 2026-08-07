"""Instagram daily upload quota tracking and enforcement."""
import sqlite3
from datetime import date


def get_instagram_posts_today(account_id: str, conn: sqlite3.Connection) -> int:
    """
    Count published Instagram posts for account today.

    Args:
        account_id: Account identifier
        conn: SQLite connection

    Returns:
        Number of published posts today
    """
    cursor = conn.execute(
        """
        SELECT COUNT(*) FROM instagram_posts
        WHERE account_id = ? AND upload_status = 'published'
        AND DATE(published_at) = DATE('now')
        """,
        (account_id,),
    )
    return cursor.fetchone()[0]


def can_post_to_instagram(
    account_id: str, conn: sqlite3.Connection, cfg
) -> bool:
    """
    Check if account can post (hasn't reached daily limit).

    Args:
        account_id: Account identifier
        conn: SQLite connection
        cfg: Config object with social_config path

    Returns:
        True if can post, False if daily limit reached
    """
    import json
    from pathlib import Path

    social_config_path = Path(cfg.project_root) / "social_config.json"
    with open(social_config_path) as f:
        social_config = json.load(f)

    daily_limit = social_config["platforms"]["instagram"]["daily_limit"]
    posted_today = get_instagram_posts_today(account_id, conn)

    return posted_today < daily_limit


def log_instagram_post(
    video_id: int,
    account_id: str,
    status: str,
    media_id: str | None,
    error_code: int | None,
    conn: sqlite3.Connection,
    ig_business_id: str = None,
    caption_text: str = None,
    hashtags: str = None,
    error_msg: str = None,
    meta_error_type: str = None,
) -> int:
    """
    Insert or update instagram_posts row. Returns instagram_posts.id

    Args:
        video_id: Foreign key to videos table
        account_id: Account identifier
        status: Upload status (pending, uploading, processing, published, failed, permanently_failed)
        media_id: Instagram media_id (if published)
        error_code: HTTP error code (if failed)
        conn: SQLite connection
        ig_business_id: Instagram business account ID
        caption_text: Caption text posted
        hashtags: Hashtags string
        error_msg: Error message
        meta_error_type: Meta API error type

    Returns:
        instagram_posts.id
    """
    cursor = conn.execute(
        """
        INSERT INTO instagram_posts (
            video_id, account_id, ig_business_id, upload_status, media_id,
            caption_text, hashtags, last_error_code, last_error_msg, meta_error_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(video_id) DO UPDATE SET
            upload_status = excluded.upload_status,
            media_id = excluded.media_id,
            last_error_code = excluded.last_error_code,
            last_error_msg = excluded.last_error_msg,
            meta_error_type = excluded.meta_error_type
        """,
        (
            video_id,
            account_id,
            ig_business_id,
            status,
            media_id,
            caption_text,
            hashtags,
            error_code,
            error_msg,
            meta_error_type,
        ),
    )
    conn.commit()

    cursor = conn.execute(
        "SELECT id FROM instagram_posts WHERE video_id = ?", (video_id,)
    )
    return cursor.fetchone()[0]


def increment_retry_count(
    instagram_post_id: int,
    conn: sqlite3.Connection,
    error_code: int | None = None,
    error_msg: str | None = None,
) -> None:
    """
    Increment retry_count for failed upload and update error tracking.

    Args:
        instagram_post_id: ID of instagram_posts row
        conn: SQLite connection
        error_code: HTTP error code
        error_msg: Error message
    """
    conn.execute(
        """
        UPDATE instagram_posts
        SET retry_count = retry_count + 1,
            last_error_code = ?,
            last_error_msg = ?
        WHERE id = ?
        """,
        (error_code, error_msg, instagram_post_id),
    )
    conn.commit()


def mark_permanently_failed(
    instagram_post_id: int,
    conn: sqlite3.Connection,
    error_code: int | None = None,
    error_msg: str | None = None,
) -> None:
    """
    Mark upload as permanently failed.

    Args:
        instagram_post_id: ID of instagram_posts row
        conn: SQLite connection
        error_code: HTTP error code
        error_msg: Error message
    """
    conn.execute(
        """
        UPDATE instagram_posts
        SET upload_status = 'permanently_failed',
            last_error_code = ?,
            last_error_msg = ?
        WHERE id = ?
        """,
        (error_code, error_msg, instagram_post_id),
    )
    conn.commit()


def mark_published(
    instagram_post_id: int,
    conn: sqlite3.Connection,
    media_id: str,
    post_url: str,
) -> None:
    """
    Mark upload as successfully published.

    Args:
        instagram_post_id: ID of instagram_posts row
        conn: SQLite connection
        media_id: Instagram media_id
        post_url: URL to published post
    """
    from datetime import datetime

    conn.execute(
        """
        UPDATE instagram_posts
        SET upload_status = 'published',
            media_id = ?,
            post_url = ?,
            published_at = ?
        WHERE id = ?
        """,
        (media_id, post_url, datetime.now().isoformat(), instagram_post_id),
    )
    conn.commit()
