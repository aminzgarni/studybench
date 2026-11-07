#!/usr/bin/env bash
# StudyBench launcher for macOS and Linux. (Windows: use run.bat.)
# First start installs what is missing; later starts just open the app.
cd "$(dirname "$0")" || exit 1

say()  { printf '\n  %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

say "StudyBench: the first start installs everything it needs. This can take a few minutes."

OS="$(uname -s)"
if [ "$OS" = "Darwin" ]; then
  # Homebrew is the usual way to install tools on a Mac; make it visible to this script.
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
    [ -x "$b" ] && eval "$("$b" shellenv)"
  done
fi

# --- Python 3.9 or newer
PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3.9 python3; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -z "$PY" ] && [ "$OS" = "Darwin" ] && have brew; then
  say "Python 3.9+ not found. Installing it with Homebrew..."
  brew install python@3.12 && PY="$(brew --prefix python@3.12)/bin/python3.12"
fi
if [ -z "$PY" ]; then
  say "Python 3.9 or newer is required but was not found."
  if [ "$OS" = "Darwin" ]; then
    echo "  Easiest: install it from https://www.python.org/downloads/macos/ , then double-click run.command again."
    echo "  Or install Homebrew (https://brew.sh) and run this launcher again; it then installs Python by itself."
    have open && open "https://www.python.org/downloads/macos/"
  else
    echo "  Install it with your package manager, e.g.: sudo apt install python3 python3-venv python3-pip"
  fi
  exit 1
fi

# --- virtual environment + packages
if [ ! -d .venv ]; then
  "$PY" -m venv .venv || { say "Could not create the virtual environment. On Debian/Ubuntu: sudo apt install python3-venv"; exit 1; }
fi
# shellcheck disable=SC1091
. .venv/bin/activate
python -m pip install -q --upgrade pip
pip install -q -r requirements.txt || { say "Installing packages failed. Check your internet connection and try again."; exit 1; }

# --- local AI (Ollama). Optional: you can also choose Claude or OpenAI in Settings.
if ! have ollama && [ ! -d /Applications/Ollama.app ]; then
  if [ "$OS" = "Darwin" ] && have brew; then
    say "Installing Ollama (local AI) with Homebrew..."
    brew install ollama || echo "  Ollama install failed. Download it from https://ollama.com/download"
  else
    say "Ollama (local AI) is not installed. Install it from https://ollama.com/download"
    echo "  Or skip it and choose Claude / OpenAI with your own key in Settings."
  fi
fi

# --- OCR for scanned PDFs (optional)
if ! have tesseract; then
  if [ "$OS" = "Darwin" ] && have brew; then
    say "Installing Tesseract (OCR for scanned PDFs) with Homebrew..."
    brew install tesseract >/dev/null 2>&1 || echo "  Tesseract install failed; scanned PDFs will not be readable."
  else
    echo "  Optional: install Tesseract for scanned PDFs (macOS: brew install tesseract; Debian/Ubuntu: sudo apt install tesseract-ocr)."
  fi
fi

say "Starting StudyBench. The AI model and the speech model download in the background"
echo "  (progress is shown in the app, bottom left). Press Ctrl+C in this window to stop."
echo
exec python app.py
