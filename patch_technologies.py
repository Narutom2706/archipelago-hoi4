import os
import re
import shutil
from pathlib import Path

HOI4_INSTALL = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV")
MOD_TECH_DIR = Path(os.path.expanduser("~")) / "Documents" / "Paradox Interactive" / "Hearts of Iron IV" / "mod" / "Archipelago" / "common" / "technologies"

MOD_TECH_DIR.mkdir(parents=True, exist_ok=True)
source_tech_dir = HOI4_INSTALL / "common" / "technologies"

year_pattern = re.compile(r"(\bstart_year\s*=\s*(\d{4}))")

count = 0
for file in source_tech_dir.glob("*.txt"):
    with open(file, "r", encoding="utf-8-sig", errors="ignore") as f:
        content = f.read()

   
    def inject_lock(match):
        full_match = match.group(1)
        year = match.group(2)
        return f"{full_match}\n\tenable = {{ custom_trigger_tooltip = {{ tooltip = \"Nécessite palier {year}\" has_country_flag = ap_tech_{year} }} }}"

    new_content, n = year_pattern.subn(inject_lock, content)
    if n > 0:
        with open(MOD_TECH_DIR / file.name, "w", encoding="utf-8") as f:
            f.write(new_content)
        count += n

print(f"[+] {count} technologies verrouillées et générées dans le dossier du mod.")