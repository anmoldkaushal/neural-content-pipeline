#!/usr/bin/env bash
# Bootstrap: creates a venv, installs deps, checks for the OCR system binaries (tesseract,
# poppler) that pip can't install for you.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [ ! -d .venv ]; then
  echo "Creating virtualenv at .venv ..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --upgrade pip >/dev/null
pip install -e ".[dev]"

echo
echo "Checking optional OCR system dependencies (needed only for scanned PDFs / images) ..."
missing=()
command -v tesseract >/dev/null 2>&1 || missing+=("tesseract")
command -v pdftoppm >/dev/null 2>&1 || missing+=("poppler")

if [ ${#missing[@]} -gt 0 ]; then
  echo "  Missing: ${missing[*]}"
  echo "  Install with: brew install tesseract poppler"
  echo "  (Not required for text-layer PDFs, .docx, or .txt/.md ingestion.)"
else
  echo "  tesseract and poppler both found."
fi

echo
echo "Setup complete. Activate with: source .venv/bin/activate"
echo "Try: python -m pipeline.cli run --client exemplar --brief tests/fixtures/sample_brief.yaml"
