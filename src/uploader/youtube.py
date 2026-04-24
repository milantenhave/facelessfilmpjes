"""Upload a rendered video to YouTube as a Short."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from ..utils.logger import get_logger
from .oauth_youtube import load_credentials

log = get_logger(__name__)

DEFAULT_CATEGORY = "22"   # "People & Blogs". 24=Entertainment, 27=Education.


@dataclass
class YouTubeUpload:
    video_id: str
    video_url: str


class YouTubeUploader:
    def __init__(self, *, chunksize: int = 1024 * 1024 * 4) -> None:
        self.chunksize = chunksize

    def upload(
        self,
        *,
        channel_id: int,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        privacy: str = "public",
        category_id: str = DEFAULT_CATEGORY,
        made_for_kids: bool = False,
        progress_cb: Optional[Callable[[float], None]] = None,
    ) -> YouTubeUpload:
        creds = load_credentials(channel_id)
        if not creds:
            raise RuntimeError(
                f"channel {channel_id} is not connected to YouTube. "
                "Run OAuth first."
            )
        youtube = build("youtube", "v3", credentials=creds,
                        cache_discovery=False)

        # YouTube Shorts is inferred from vertical aspect ratio + <60s.
        # We add #Shorts to the description as a best-practice nudge.
        if "#shorts" not in description.lower():
            description = (description.rstrip() + "\n\n#Shorts").strip()

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:4900],
                "tags": [t.lstrip("#")[:30] for t in tags][:15],
                "categoryId": category_id,
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": made_for_kids,
                "embeddable": True,
            },
        }

        media = MediaFileUpload(
            str(video_path),
            chunksize=self.chunksize,
            resumable=True,
            mimetype="video/mp4",
        )
        request = youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status and progress_cb:
                progress_cb(float(status.progress()) * 100.0)

        vid = response["id"]
        url = f"https://www.youtube.com/shorts/{vid}"
        log.info("uploaded to YouTube: %s", url)
        return YouTubeUpload(video_id=vid, video_url=url)
