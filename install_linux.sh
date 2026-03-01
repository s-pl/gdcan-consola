#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
LAUNCHER_DIR="$HOME/.local/bin"
LAUNCHER="$LAUNCHER_DIR/gdcan-consola"

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Error: no se encontró '$PYTHON_BIN'."
  echo "Instala Python 3 y vuelve a ejecutar este script."
  exit 1
fi

echo "[1/4] Instalando dependencias Python..."
"$PYTHON_BIN" -m pip install --user --upgrade pip
"$PYTHON_BIN" -m pip install --user playwright textual rich

echo "[2/4] Instalando Chromium para Playwright..."
"$PYTHON_BIN" -m playwright install chromium

echo "[3/4] Creando lanzador en $LAUNCHER..."
mkdir -p "$LAUNCHER_DIR"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$PYTHON_BIN" "$PROJECT_DIR/main.py" "\$@"
EOF
chmod +x "$LAUNCHER"

echo "[4/4] Asegurando PATH (~/.local/bin)..."
for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do
  touch "$rc"
  if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$rc"; then
    printf '\n# gdcan-consola\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
  fi
done

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
  export PATH="$HOME/.local/bin:$PATH"
fi

echo
echo "Instalación completada."
echo "Puedes ejecutar: gdcan-consola"
echo "Si no lo reconoce la terminal, abre una nueva sesión."
