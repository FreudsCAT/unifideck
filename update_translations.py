import json
import os

locales_dir = "/home/deck/Documents/Projects/unifideck-main/unifideck-decky/src/i18n/locales"

translations = {
    "en-US.json": "{{title}} is ready to play.",
    "de-DE.json": "{{title}} ist spielbereit.",
    "es-ES.json": "{{title}} está listo para jugar.",
    "fr-FR.json": "{{title}} est prêt à jouer.",
    "it-IT.json": "{{title}} è pronto per giocare.",
    "ja-JP.json": "{{title}} の準備ができました。",
    "ko-KR.json": "{{title}}을(를) 플레이할 준비가 되었습니다.",
    "nl-NL.json": "{{title}} is klaar om te spelen.",
    "pl-PL.json": "{{title}} jest gotowy do gry.",
    "pt-BR.json": "{{title}} está pronto para jogar.",
    "ru-RU.json": "{{title}} готов к игре.",
    "tr-TR.json": "{{title}} oynamaya hazır.",
    "uk-UA.json": "{{title}} готова до гри.",
    "zh-CN.json": "{{title}} 已准备好游玩。"
}

for filename, trans in translations.items():
    path = os.path.join(locales_dir, filename)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        
        # update the nested key
        if "toasts" in data and "installCompleteMessage" in data["toasts"]:
            data["toasts"]["installCompleteMessage"] = trans
            
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                # json.dump doesn't add a trailing newline, so we add one
                f.write("\n")
            print(f"Updated {filename}")
        else:
            print(f"Skipped {filename} (key not found)")
    else:
        print(f"File not found: {filename}")
