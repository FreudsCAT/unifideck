#!/usr/bin/env python3
"""
Ubisoft Language Setter — Sets Windows locale and UPC language in Wine prefix.

Usage: python3 ubisoft_set_language.py <prefix_path> [space_id]

Sets two things:
  1. Windows locale in HKCU\\Control Panel\\International (user.reg)
     — so the game detects the correct language via Windows APIs
  2. UPC install language in HKLM\\..\\Ubisoft\\Launcher\\Installs\\{id}\\Language
     — so UPC knows which language pack to use

Follows the same pattern as amazon_set_language.py.

Reference: docs/ubisoft-store-spec.md §7.5 (Language Settings)
"""
import json
import os
import re
import sys

# Mapping from Unifideck language codes to Windows locale data
# Format: { code: (LCID_hex, sLanguage_3letter, LocaleName, sCountry) }
LOCALE_MAP = {
    'en-US': ('00000409', 'ENU', 'en-US', 'United States'),
    'de-DE': ('00000407', 'DEU', 'de-DE', 'Germany'),
    'fr-FR': ('0000040c', 'FRA', 'fr-FR', 'France'),
    'es-ES': ('00000c0a', 'ESN', 'es-ES', 'Spain'),
    'it-IT': ('00000410', 'ITA', 'it-IT', 'Italy'),
    'pt-BR': ('00000416', 'PTB', 'pt-BR', 'Brazil'),
    'ru-RU': ('00000419', 'RUS', 'ru-RU', 'Russia'),
    'pl-PL': ('00000415', 'PLK', 'pl-PL', 'Poland'),
    'zh-CN': ('00000804', 'CHS', 'zh-CN', 'China'),
    'ja-JP': ('00000411', 'JPN', 'ja-JP', 'Japan'),
    'ko-KR': ('00000412', 'KOR', 'ko-KR', 'Korea'),
    'nl-NL': ('00000413', 'NLD', 'nl-NL', 'Netherlands'),
    'tr-TR': ('0000041f', 'TRK', 'tr-TR', 'Turkey'),
}

# Ubisoft uses 2-letter codes for its Language registry key
UBISOFT_LANG_MAP = {
    'en-US': 'en', 'de-DE': 'de', 'fr-FR': 'fr', 'es-ES': 'es',
    'it-IT': 'it', 'pt-BR': 'pt', 'ru-RU': 'ru', 'pl-PL': 'pl',
    'zh-CN': 'zh', 'ja-JP': 'ja', 'ko-KR': 'ko', 'nl-NL': 'nl',
    'tr-TR': 'tr',
}


def get_unifideck_language() -> str:
    """Get the user's preferred language from Unifideck settings."""
    settings_path = os.path.expanduser("~/.local/share/unifideck/settings.json")
    try:
        if os.path.exists(settings_path):
            with open(settings_path, 'r') as f:
                settings = json.load(f)
                lang = settings.get('language', 'en-US')
                if lang and lang != 'auto':
                    return lang
    except Exception as e:
        print(f"Could not read settings: {e}")
    return 'en-US'


def smart_match_locale(target: str) -> tuple | None:
    """Find best locale match for the target language code."""
    if not target:
        return None

    # Exact match
    if target in LOCALE_MAP:
        return LOCALE_MAP[target]

    # Base language match (e.g., 'en' matches 'en-US')
    target_base = target.split('-')[0].lower()
    for code, data in LOCALE_MAP.items():
        if code.split('-')[0].lower() == target_base:
            return data

    return None


def update_user_reg(prefix_path: str, lcid: str, slanguage: str, locale_name: str, scountry: str):
    """Update the Wine prefix user.reg to set Windows locale."""
    user_reg = os.path.join(prefix_path, 'user.reg')

    if not os.path.exists(user_reg):
        print(f"user.reg not found at {user_reg} - prefix may not be initialized yet")
        return False

    with open(user_reg, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    section_header = '[Control Panel\\\\International]'

    new_values = {
        'Locale': lcid,
        'LocaleName': locale_name,
        'sLanguage': slanguage,
        'sCountry': scountry,
    }

    if section_header in content:
        section_start = content.index(section_header)
        next_section = re.search(r'\n\[', content[section_start + len(section_header):])
        if next_section:
            section_end = section_start + len(section_header) + next_section.start()
        else:
            section_end = len(content)

        section_body = content[section_start + len(section_header):section_end]

        for key, value in new_values.items():
            pattern = rf'^"{re.escape(key)}"="[^"]*"'
            replacement = f'"{key}"="{value}"'
            new_body, count = re.subn(pattern, replacement, section_body, flags=re.MULTILINE)
            if count > 0:
                section_body = new_body
                print(f"  Updated {key}={value}")
            else:
                section_body = section_body.rstrip('\n') + f'\n"{key}"="{value}"\n'
                print(f"  Added {key}={value}")

        content = content[:section_start + len(section_header)] + section_body + content[section_end:]
    else:
        print(f"  Creating {section_header} section")
        new_section = f'\n{section_header}\n'
        for key, value in new_values.items():
            new_section += f'"{key}"="{value}"\n'
            print(f"  Added {key}={value}")
        content += new_section

    with open(user_reg, 'w', encoding='utf-8') as f:
        f.write(content)

    return True


def update_ubisoft_language_reg(prefix_path: str, space_id: str, lang_code: str) -> bool:
    """
    Update UPC's per-game Language registry key in system.reg.

    Sets HKLM\\Software\\WOW6432Node\\Ubisoft\\Launcher\\Installs\\{install_id}\\Language.
    """
    # Resolve install_id from space_id
    id_map_path = os.path.expanduser("~/.local/share/unifideck/ubisoft_id_map.json")
    install_id = None
    if os.path.isfile(id_map_path):
        try:
            with open(id_map_path, 'r') as f:
                id_map = json.load(f)
            entry = id_map.get(space_id, {})
            install_id = entry.get('install_id')
        except Exception:
            pass

    if not install_id:
        print(f"  No install_id found for space_id={space_id}, skipping UPC language")
        return False

    # Get 2-letter code
    ubi_lang = UBISOFT_LANG_MAP.get(lang_code, lang_code.split('-')[0])

    system_reg = os.path.join(prefix_path, 'system.reg')
    if not os.path.isfile(system_reg):
        print(f"  system.reg not found, skipping UPC language")
        return False

    with open(system_reg, 'r', encoding='utf-8', errors='replace') as f:
        content = f.read()

    section = f'[Software\\\\WOW6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\{install_id}]'

    if section in content:
        sec_start = content.index(section)
        next_sec = re.search(r'\n\[', content[sec_start + len(section):])
        sec_end = sec_start + len(section) + next_sec.start() if next_sec else len(content)
        sec_body = content[sec_start + len(section):sec_end]

        pattern = r'^"Language"="[^"]*"'
        replacement = f'"Language"="{ubi_lang}"'
        new_body, count = re.subn(pattern, replacement, sec_body, flags=re.MULTILINE)
        if count > 0:
            sec_body = new_body
        else:
            sec_body = sec_body.rstrip('\n') + f'\n"Language"="{ubi_lang}"\n'

        content = content[:sec_start + len(section)] + sec_body + content[sec_end:]
    else:
        content += f'\n{section}\n"Language"="{ubi_lang}"\n'

    with open(system_reg, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"  UPC language set to: {ubi_lang} (install_id={install_id})")
    return True


def main():
    if len(sys.argv) < 2:
        print("Usage: ubisoft_set_language.py <prefix_path> [space_id]")
        sys.exit(1)

    prefix_path = sys.argv[1]
    space_id = sys.argv[2] if len(sys.argv) > 2 else ""

    print(f"Ubisoft Language Setter")
    print(f"Prefix: {prefix_path}")

    # Auto-detect prefix layout: Proton uses <prefix>/pfx/
    user_reg_direct = os.path.join(prefix_path, 'user.reg')
    user_reg_pfx = os.path.join(prefix_path, 'pfx', 'user.reg')
    if not os.path.exists(user_reg_direct) and os.path.exists(user_reg_pfx):
        prefix_path = os.path.join(prefix_path, 'pfx')
        print(f"Using pfx subdirectory: {prefix_path}")
    elif not os.path.exists(user_reg_direct) and os.path.isdir(os.path.join(prefix_path, 'pfx')):
        prefix_path = os.path.join(prefix_path, 'pfx')
        print(f"Using pfx subdirectory (prefix not fully initialized): {prefix_path}")

    # Get user's preferred language
    preferred_lang = get_unifideck_language()
    print(f"User preferred language: {preferred_lang}")

    # Find matching locale data
    locale_data = smart_match_locale(preferred_lang)
    if not locale_data:
        print(f"No locale mapping for {preferred_lang}, defaulting to en-US")
        locale_data = LOCALE_MAP['en-US']

    lcid, slanguage, locale_name, scountry = locale_data
    print(f"Setting Windows locale: {locale_name} (LCID={lcid}, sLanguage={slanguage})")

    # Update Windows locale in user.reg
    if update_user_reg(prefix_path, lcid, slanguage, locale_name, scountry):
        print("Windows locale updated successfully!")
    else:
        print("Could not update Windows locale (prefix may not exist yet)")

    # Update UPC language in system.reg (if space_id provided)
    if space_id:
        update_ubisoft_language_reg(prefix_path, space_id, preferred_lang)


if __name__ == '__main__':
    main()
