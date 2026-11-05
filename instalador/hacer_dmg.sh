#!/usr/bin/env bash
# Arma el .dmg de iik para macOS a partir de dist/iik.app (lo que deja PyInstaller).
#
#     pyinstaller instalador/iik.spec --noconfirm
#     bash instalador/hacer_dmg.sh 0.1.0
#
# Resultado: dist/iik-<versión>-mac-apple-silicon.dmg, con la app, un acceso a Aplicaciones y las
# instrucciones para abrirla la primera vez (la app no está firmada con un certificado de Apple).
set -euo pipefail
VERSION="${1:-0.1.0}"
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
APP="$RAIZ/dist/iik.app"
DMG="$RAIZ/dist/iik-$VERSION-mac-apple-silicon.dmg"

test -d "$APP" || { echo "No existe $APP: corra primero PyInstaller"; exit 1; }

# Firma «ad hoc» de todo el paquete (Apple Silicon no abre binarios sin firma alguna).
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

PREP="$(mktemp -d)"
trap 'rm -rf "$PREP"' EXIT
cp -R "$APP" "$PREP/"
ln -s /Applications "$PREP/Aplicaciones"
cp "$RAIZ/instalador/LEEME-mac.txt" "$PREP/LEEME - abrir la primera vez.txt"

rm -f "$DMG"
hdiutil create -volname "iik $VERSION" -srcfolder "$PREP" -fs HFS+ -format UDZO -ov "$DMG"
echo "Listo: $DMG"
