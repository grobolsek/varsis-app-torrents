"""ORM model of the torrents plugin: TorrentJob. Its table is created with the app's own at startup."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column

from ...db import Base


class TorrentJob(Base):
    """A torrent download a user started. The poller in torrents.py follows it until it is imported or fails."""

    __tablename__ = "torrent_jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str]
    status: Mapped[str] = mapped_column(default="downloading")  # downloading | importing | done | failed
    progress: Mapped[float] = mapped_column(default=0)  # 0 to 1, as qBittorrent reports it
    message: Mapped[str | None]  # error or import summary
    details: Mapped[dict | None] = mapped_column(JSON)  # search result info + the .torrent's file list
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
