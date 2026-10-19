#!/bin/bash
# Создаёт два ярлыка AI Link в меню приложений:
#   - AI Link (обычный)
#   - AI Link (с терминалом, для отладки)
#
# Запуск: bash install_launchers.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$SCRIPT_DIR/приложение"
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"

if [ ! -f "$APP_DIR/main.py" ]; then
    echo "ОШИБКА: не найден $APP_DIR/main.py"
    echo "Запустите скрипт из папки ai-link/"
    exit 1
fi

cat > "$DESKTOP_DIR/ai-link.desktop" << EOF
[Desktop Entry]
Version=1.0
Name=AI Link
Comment=Оркестратор LLM-агентов
Exec=python3 $APP_DIR/main.py
Icon=$APP_DIR/resources/ai_link.svg
Terminal=false
Type=Application
Categories=Development;Utility;
StartupNotify=true
EOF

cat > "$DESKTOP_DIR/ai-link-terminal.desktop" << EOF
[Desktop Entry]
Version=1.0
Name=AI Link (с терминалом)
Comment=Оркестратор LLM-агентов — отладка
Exec=gnome-terminal -- bash -c "cd $APP_DIR && python3 main.py; echo; echo 'Нажмите Enter...'; read"
Icon=$APP_DIR/resources/ai_link.svg
Terminal=true
Type=Application
Categories=Development;Utility;
StartupNotify=true
EOF

chmod +x "$DESKTOP_DIR/ai-link.desktop" "$DESKTOP_DIR/ai-link-terminal.desktop"
update-desktop-database "$DESKTOP_DIR" 2>/dev/null

echo "OK: ярлыки установлены в меню приложений:"
echo "  - AI Link"
echo "  - AI Link (с терминалом)"
