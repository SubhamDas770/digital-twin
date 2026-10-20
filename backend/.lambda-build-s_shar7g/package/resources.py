from pypdf import PdfReader
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"

# Read LinkedIn PDF
try:
    reader = PdfReader(DATA_DIR / "linkedin.pdf")
    linkedin = ""
    for page in reader.pages:
        text = page.extract_text()
        if text:
            linkedin += text
except FileNotFoundError:
    linkedin = "LinkedIn profile not available"

# Read other data files
with (BASE_DIR / "summary.txt").open("r", encoding="utf-8") as f:
    summary = f.read()

with (DATA_DIR / "style.txt").open("r", encoding="utf-8") as f:
    style = f.read()

with (DATA_DIR / "facts.json").open("r", encoding="utf-8") as f:
    facts = json.load(f)
