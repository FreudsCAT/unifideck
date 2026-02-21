import re

words = [
    # English
    "Play", "Install", "Stream", "Resume", "Update", "Pre-load", "PreLoad", "Downloading", "Download",
    # Spanish
    "Jugar", "Instalar", "Transmitir", "Reanudar", "Actualizar", "Precarga", "Descargando", "Descargar",
    # French
    "Jouer", "Installer", "Streamer", "Reprendre", "Mettre à jour", "Précharger", "Téléchargement", "Télécharger",
    # German
    "Spielen", "Installieren", "Streamen", "Fortsetzen", "Aktualisieren", "Vorab laden", "Lädt herunter", "Herunterladen",
    # Italian
    "Gioca", "Installa", "Trasmetti", "Riprendi", "Aggiorna", "Precarica", "In download", "Scarica",
    # Portuguese (Brazil)
    "Jogar", "Instalar", "Transmitir", "Retomar", "Atualizar", "Pré-carregar", "Pré-carregamento", "Baixando", "Baixar",
    # Russian
    "Играть", "Установить", "Транслировать", "Возобновить", "Обновить", "Предзагрузка", "Загрузка", "Скачать",
    # Polish
    "Graj", "Zainstaluj", "Strumieniuj", "Wznów", "Aktualizuj", "Wstępne pobieranie", "Pobieranie", "Pobierz",
    # Turkish
    "Oyna", "Yükle", "Yayınla", "Devam Et", "Güncelle", "Ön Yükleme", "İndiriliyor", "İndir",
    # Dutch
    "Spelen", "Installeren", "Streamen", "Hervatten", "Updaten", "Vooraf laden", "Downloaden",
    # Japanese
    "プレイ", "インストール", "ストリーミング", "再開", "アップデート", "プリロード", "ダウンロード中", "ダウンロード",
    # Korean
    "플레이", "설치", "스트리밍", "다시 시작", "업데이트", "사전 로드", "다운로드 중", "다운로드",
    # Chinese (Simplified)
    "开始游戏", "安装", "流式传输", "恢复", "更新", "预载", "下载中", "下载",
    # Chinese (Traditional)
    "執行", "安裝", "串流", "繼續", "預載", "下載中", "下載",
    # Common alternate casings/spellings
    "Pre-Load"
]

# Deduplicate
unique_words = sorted(list(set(words)))

# Build regex string
regex_inner = "|".join(unique_words)

# Ensure the string is properly escaped if needed, but for python raw string it should be fine.
# The quote format in cdp_inject.py is:
# '        if (/^(Play|Install|Stream|Resume|Update|Pre-load|Pre-Load|Downloading|Download)$/i.test(txt)) {\n'

file_path = "py_modules/unifideck/cdp/cdp_inject.py"

with open(file_path, "r", encoding="utf-8") as f:
    content = f.read()

old_line = "        if (/^(Play|Install|Stream|Resume|Update|Pre-load|Pre-Load|Downloading|Download)$/i.test(txt)) {"
new_line = f"        if (/^({regex_inner})$/i.test(txt)) {{"

if old_line in content:
    content = content.replace(old_line, new_line)
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Successfully replaced regex.")
else:
    print("Could not find the target string to replace.")

