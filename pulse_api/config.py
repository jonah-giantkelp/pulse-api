from pathlib import Path

from pydantic_settings import BaseSettings

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    # Supabase
    supabase_url: str
    supabase_key: str

    # Spotify
    spotify_client_id: str = ""
    spotify_client_secret: str = ""

    # Ticketmaster
    ticketmaster_api_key: str = ""

    # Bandsintown
    bandsintown_app_id: str = ""

    # RapidAPI keys (one per service)
    rapidapi_instagram_key: str = ""
    rapidapi_twitter_key: str = ""
    rapidapi_concerts_key: str = ""

    # Skiddle
    skiddle_api_key: str = ""

    # LLM
    openai_api_key: str = ""
    anthropic_api_key: str = ""

    # Jina Reader (used only by the website scraper for page→markdown)
    jina_api_key: str = ""

    # Sync endpoint key (for cron trigger)
    sync_api_key: str = ""

    # Dev mode (bypasses auth, uses a fixed test user ID)
    dev_mode: bool = False
    dev_user_id: str = "00000000-0000-0000-0000-000000000000"

    # Webshare proxy
    webshare_proxy_host: str = ""
    webshare_proxy_port: str = "80"
    webshare_api_key: str = ""

    # Test auth credentials
    test_email: str = ""
    test_password: str = ""

    # Postmark (email digest)
    postmark_server_token: str = ""
    postmark_from_email: str = "digest@pulse.app"

    # Defaults
    default_city: str = ""

    model_config = {"env_file": str(ENV_FILE), "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()
