"""config.py — Frozen ``GOGConfig`` value-object.

# OP-50b | py_modules/unifideck/stores/gog/config.py | Depends: (none)

OAuth credentials default to empty strings; deployments inject the
real client_id / client_secret via the user's ``stores.gog.*`` config
overrides. ``is_valid()`` returns True only when both are populated.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from ...config import ConfigManager

logger = logging.getLogger(__name__)
_GOG_CONFIG_PREFIX = 'stores.gog'
_DEFAULT_TOKEN_FILE = '~/.config/unifideck/gog_token.json'
_DEFAULT_GOGDL_CONFIG_DIR = '~/.config/unifideck/gogdl'
_DEFAULT_DOWNLOAD_DIR = '~/GOG Games'
GOG_AUTH_URL_FILE = '~/.local/share/unifideck/gog_auth_url.txt'


@dataclass(frozen=True)
class GOGConfig:
    """GOG config."""

    client_id: str = ''
    client_secret: str = ''
    auth_url: str = ''
    token_url: str = ''
    redirect_uri: str = ''
    allowed_redirect_uris: list[str] = field(default_factory=list)
    base_url: str = ''
    api_gog_url: str = ''
    token_file: str = _DEFAULT_TOKEN_FILE
    gogdl_config_dir: str = _DEFAULT_GOGDL_CONFIG_DIR
    download_dir: str = _DEFAULT_DOWNLOAD_DIR
    token_refresh_threshold_seconds: int = 2400
    supported_languages: list[str] = field(
        default_factory=lambda: [
            'en', 'de', 'fr', 'pl', 'ru', 'pt', 'es', 'it', 'zh', 'ko', 'ja',
        ],
    )
    user_agent: str = 'Unifideck/1.0'

    @classmethod
    def from_config_manager(
        cls, config: ConfigManager | None,
    ) -> GOGConfig:
        """From config manager."""

        def _str(key: str, default: str) -> str:
            value = get_cfg(config, f'{_GOG_CONFIG_PREFIX}.{key}', default)
            return str(value) if value is not None else default

        def _int(key: str, default: int) -> int:
            value = get_cfg(config, f'{_GOG_CONFIG_PREFIX}.{key}', default)
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def _list(key: str, default: list[str]) -> list[str]:
            value = get_cfg(config, f'{_GOG_CONFIG_PREFIX}.{key}', default)
            if isinstance(value, (list, tuple)):
                return [str(v) for v in value]
            return list(default)

        return cls(
            client_id=_str('client_id', ''),
            client_secret=_str('client_secret', ''),
            auth_url=_str('auth_url', ''),
            token_url=_str('token_url', ''),
            redirect_uri=_str('redirect_uri', ''),
            allowed_redirect_uris=_list('allowed_redirect_uris', []),
            base_url=_str('base_url', ''),
            api_gog_url=_str('api_gog_url', ''),
            token_file=_str('token_file', _DEFAULT_TOKEN_FILE),
            gogdl_config_dir=_str(
                'gogdl_config_dir', _DEFAULT_GOGDL_CONFIG_DIR,
            ),
            download_dir=_str('download_dir', _DEFAULT_DOWNLOAD_DIR),
            token_refresh_threshold_seconds=_int(
                'token_refresh_threshold_seconds', 2400,
            ),
            supported_languages=_list(
                'supported_languages',
                ['en', 'de', 'fr', 'pl', 'ru', 'pt', 'es', 'it', 'zh', 'ko', 'ja'],
            ),
            user_agent=_str('user_agent', 'Unifideck/1.0'),
        )

    def is_valid(self) -> bool:
        """Is valid."""
        return bool(self.client_id and self.client_secret)

    @property
    def auth_config_path(self) -> str:
        """Auth config path."""
        return os.path.expanduser(GOG_AUTH_URL_FILE)

    def describe(self) -> str:
        """Describe."""
        return (
            f'GOGConfig(token_file={os.path.expanduser(self.token_file)}, '
            f'download_dir={os.path.expanduser(self.download_dir)}, '
            f'valid={self.is_valid()})'
        )
