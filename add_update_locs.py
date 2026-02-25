import os
import json

locales_dir = "/home/deck/Documents/Projects/unifideck-main/unifideck-decky/src/i18n/locales"
for f in os.listdir(locales_dir):
    if f.endswith('.json'):
        path = os.path.join(locales_dir, f)
        with open(path, 'r', encoding='utf-8') as file:
            data = json.load(file)
            
        if "toasts" in data:
            if "updateComplete" not in data["toasts"]:
                data["toasts"]["updateComplete"] = "Update Complete!"
            if "updateCompleteMessage" not in data["toasts"]:
                data["toasts"]["updateCompleteMessage"] = "{{title}} has been updated."
        
        with open(path, 'w', encoding='utf-8') as file:
            json.dump(data, file, indent=2, ensure_ascii=False)
            file.write('\n')
print("Successfully patched locales")
