import json
import os

locales_dir = "/home/deck/Documents/Projects/unifideck-main/unifideck-decky/src/i18n/locales"
en_us_path = os.path.join(locales_dir, "en-US.json")

with open(en_us_path, 'r', encoding='utf-8') as f:
    en_us = json.load(f)

def get_keys(d, prefix=""):
    keys = set()
    for k, v in d.items():
        if isinstance(v, dict):
            keys.update(get_keys(v, f"{prefix}{k}."))
        else:
            keys.add(f"{prefix}{k}")
    return keys

en_keys = get_keys(en_us)

for filename in sorted(os.listdir(locales_dir)):
    if filename.endswith(".json") and filename != "en-US.json":
        path = os.path.join(locales_dir, filename)
        with open(path, 'r', encoding='utf-8') as f:
            locale_data = json.load(f)
        
        loc_keys = get_keys(locale_data)
        missing = en_keys - loc_keys
        if missing:
            print(f"Missing in {filename}:")
            for k in sorted(missing):
                print(f"  - {k}")
