"""Settings of the torrents plugin, read from environment variables / .env like the app's own."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Where Prowlarr and qBittorrent are, and where downloads are saved.

    Each field is read from the environment variable of the same name in upper case (`PROWLARR_URL`…), or from
    the same `.env` file the app reads.
    """

    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    prowlarr_url: str = "http://localhost:9696"
    prowlarr_api_key: str = ""  # empty turns torrent search off
    qbit_url: str = "http://localhost:8081"
    qbit_user: str = "admin"
    qbit_pass: str = ""
    # qBittorrent is told to save here and the backend reads the files from here, so both must see this folder
    # under the same path.
    downloads_dir: str = "/downloads"


# Read once at import.
settings = Settings()
