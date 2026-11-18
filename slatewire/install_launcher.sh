#!/bin/bash
# Run this once after unzipping, from inside the slatewire folder:
#   bash install_launcher.sh
# It registers SLATEWIRE in your application menu. After that, launch it
# from Whisker Menu / app search like any other installed program.

set -e
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
chmod +x "$DIR/run.sh"

mkdir -p "$HOME/.local/share/applications"
cat > "$HOME/.local/share/applications/slatewire.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=SLATEWIRE
Comment=Cybersecurity news dashboard
Exec=$DIR/run.sh
Path=$DIR
Icon=utilities-terminal
Terminal=false
Categories=Utility;Security;
EOF
chmod +x "$HOME/.local/share/applications/slatewire.desktop"

echo "Installed. Search for \"SLATEWIRE\" in your Whisker Menu / application finder."
echo "(Using a generic icon for now -- swap the Icon= line in"
echo "~/.local/share/applications/slatewire.desktop for a custom one anytime.)"
