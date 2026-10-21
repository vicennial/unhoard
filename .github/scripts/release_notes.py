"""Print the CHANGELOG.md section of one version (without its heading); fail when there is none or it is empty."""
import re
import sys
from pathlib import Path

version = sys.argv[1]
text = Path(__file__).resolve().parents[2].joinpath("CHANGELOG.md").read_text(encoding="utf-8")
match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: )", text, re.M | re.S)
notes = match.group(1).strip() if match else ""
if not notes:
    sys.exit(f"CHANGELOG.md has no notes for {version}")
print(notes)
