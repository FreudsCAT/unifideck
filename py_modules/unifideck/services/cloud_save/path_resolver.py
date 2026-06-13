import os
import re
import configparser
import logging

logger = logging.getLogger(__name__)

class WinePrefixResolver:
    """Helper to resolve Windows registry path variables in Wine prefixes."""

    @staticmethod
    def read_registry(wine_pfx: str) -> configparser.ConfigParser:
        reg = configparser.ConfigParser(
            comment_prefixes=(';', '#', '/', 'WINE'),
            allow_no_value=True,
            strict=False,
            interpolation=None
        )
        reg.optionxform = str
        reg.read(os.path.join(wine_pfx, 'user.reg'))
        return reg

    @staticmethod
    def get_shell_folders(registry: configparser.ConfigParser, wine_pfx: str) -> dict[str, str]:
        folders = dict()
        section = 'Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\Shell Fold Folders'
        if section not in registry:
            section = 'Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders'
        
        if section in registry:
            for k, v in registry[section].items():
                key_name = k.strip('"').strip()
                path_cleaned = v.strip('"').strip().replace('\\\\', '/').replace('C:/', '')
                folders[key_name] = os.path.join(wine_pfx, 'drive_c', path_cleaned)
        return folders

    @classmethod
    def resolve_path(cls, cloud_save_folder: str, prefix_path: str, install_path: str = "", account_id: str = "") -> str:
        # Normalize slashes
        folder = cloud_save_folder.replace('\\', '/').strip('/')
        
        # Default shell folder mappings (if registry parsing fails or is incomplete)
        #
        # NOTE: Epic's ``{AppData}`` cloud-save token resolves to
        # %LOCALAPPDATA% (AppData/Local), NOT %APPDATA% (Roaming). Confirmed
        # on real games: Felix The Reaper and Ghostrunner both ship a
        # ``{AppData}/...`` CloudSaveFolder yet read/write saves under
        # AppData/Local — mapping it to Roaming dropped the cloud save where
        # the game never looks (Continue stayed greyed out).
        path_vars = {
            '{appdata}': os.path.join(prefix_path, 'drive_c/users/steamuser/AppData/Local'),
            '{localappdata}': os.path.join(prefix_path, 'drive_c/users/steamuser/AppData/Local'),
            '{userdir}': os.path.join(prefix_path, 'drive_c/users/steamuser/Documents'),
            '{userprofile}': os.path.join(prefix_path, 'drive_c/users/steamuser'),
            '{usersavedgames}': os.path.join(prefix_path, 'drive_c/users/steamuser/Saved Games'),
            '{installdir}': install_path,
            # Epic's ``{EpicID}`` token is the logged-in user's Epic ACCOUNT
            # id — NOT the game's catalog/app id. legendary resolves it the
            # same way (``self.lgd.userdata['account_id']``, core.py:834).
            # Games like Vampire Survivors / Brotato namespace saves under
            # ``Roaming/<Game>/<AccountId>/``; feeding the app id here pointed
            # the sync at a folder the game never reads (saves never appeared).
            '{epicid}': account_id,
            '{epic_id}': account_id,
        }
        
        # Try to read from user.reg
        user_reg_path = os.path.join(prefix_path, 'user.reg')
        if os.path.isfile(user_reg_path):
            try:
                reg = cls.read_registry(prefix_path)
                folders = cls.get_shell_folders(reg, prefix_path)
                if folders:
                    # Epic {AppData} == Local AppData (see note above), so
                    # both tokens resolve to the registry's Local AppData.
                    if 'Local AppData' in folders:
                        path_vars['{appdata}'] = folders['Local AppData']
                        path_vars['{localappdata}'] = folders['Local AppData']
                    if 'Personal' in folders:
                        path_vars['{userdir}'] = folders['Personal']
                    if '{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}' in folders:
                        path_vars['{usersavedgames}'] = folders['{4C5C32FF-BB9D-43B0-B5B4-2D72E54EAAA4}']
            except Exception as e:
                logger.error("Failed to read registry: %s", e)
        
        # Add common aliases/variations
        path_vars['{locallow}'] = os.path.join(prefix_path, 'drive_c/users/steamuser/AppData/LocalLow')
        
        # Split folder template into components
        parts = folder.split('/')
        resolved_parts = []
        for p in parts:
            p_lower = p.lower()
            if p_lower in path_vars:
                resolved_parts.append(path_vars[p_lower])
            elif p_lower == '%userprofile%':
                resolved_parts.append(path_vars['{userprofile}'])
            else:
                resolved_parts.append(p)
        
        # Join the resolved parts
        resolved_path = os.path.join(*resolved_parts)
        resolved_path = os.path.normpath(resolved_path)
        
        # De-duplicate nested path issues (e.g. AppData/LocalLow nested multiple times)
        if 'LocalLow' in resolved_path:
            match = re.search(r'LocalLow/(?:drive_c/users/[^/]+/AppData/LocalLow/)?(.*)', resolved_path, re.IGNORECASE)
            if match:
                game_subpath = match.group(1)
                resolved_path = os.path.join(path_vars['{locallow}'], game_subpath)
        
        return os.path.realpath(resolved_path)
