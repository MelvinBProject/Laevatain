import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str = field(repr=False)
    discord_guild_id: int | None
    lavalink_uri: str
    lavalink_password: str = field(repr=False)
    lavalink_identifier: str
    idle_timeout: int
    owner_id: int
    host: str
    port: int
    bot_enabled: bool
    admin_username: str
    admin_password: str = field(repr=False)
    allow_registration: bool
    db_path: Path
    demo_mode: bool = False
    deployment_mode: bool = False


def load_settings(require_token=False):
    load_dotenv(ROOT / '.env', override=False)
    data = ROOT / 'data'
    paths = {
        'LEVIATAN_DB_PATH': data / 'laevatain.db',
        'LEVIATAN_CHANNELS_PATH': data / 'music_channels.json',
        'LEVIATAN_PANELS_PATH': data / 'panel_messages.json',
        'LEVIATAN_PLAYLIST_MEDIA_PATH': data / 'playlist_media',
        'LEVIATAN_PROFILE_MEDIA_PATH': data / 'profile_media',
        'LEVIATAN_GIFS_PATH': ROOT / 'gifs',
    }
    for name, value in paths.items():
        os.environ.setdefault(name, str(value))
    token = os.getenv('DISCORD_TOKEN', '').strip()
    guild = os.getenv('DISCORD_GUILD_ID', '').strip()
    if require_token and not token:
        raise RuntimeError('Guarda el token nuevo en el archivo .env local')
    return Settings(
        token, int(guild) if guild else None,
        os.getenv('LAVALINK_URI', 'http://127.0.0.1:2334'),
        os.getenv('LAVALINK_PASSWORD', ''),
        os.getenv('LAVALINK_IDENTIFIER', 'laevatain-lab'),
        max(60, int(os.getenv('LEVIATAN_IDLE_TIMEOUT', '300'))),
        int(os.getenv('OWNER_DISCORD_ID', '0')),
        os.getenv('WEB_HOST', '127.0.0.1'), int(os.getenv('WEB_PORT', '8088')),
        os.getenv('BOT_ENABLED', 'false').lower() == 'true',
        os.getenv('ADMIN_USERNAME', 'admin'), os.getenv('ADMIN_PASSWORD', ''),
        os.getenv('ALLOW_REGISTRATION', 'false').lower() == 'true',
        Path(os.environ['LEVIATAN_DB_PATH']),
        os.getenv('DEMO_MODE', 'false').lower() == 'true',
        os.getenv('DEPLOYMENT_MODE', 'false').lower() == 'true',
    )
