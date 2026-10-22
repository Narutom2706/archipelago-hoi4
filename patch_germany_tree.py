import json
import os
import re
import sys
from pathlib import Path

STEAM_HOI4 = Path(r"C:\Program Files (x86)\Steam\steamapps\common\Hearts of Iron IV")
SOURCE_FILE = STEAM_HOI4 / "common" / "national_focus" / "germany.txt"

DEST_DIR = Path(os.path.expanduser("~")) / "Documents" / "Paradox Interactive" / "Hearts of Iron IV" / "mod" / "Archipelago" / "common" / "national_focus"
DEST_FILE = DEST_DIR / "germany.txt"
MAPPING_FILE = Path(__file__).parent / "mapping_germany.json"

ALT_ROOTS = {
    "GER_oppose_hitler",
    "GER_oppose_hitler_ww",
    "GER_prioritize_economic_growth",
}

if not SOURCE_FILE.exists():
    alt = input("Path to Germany.txt : ").strip('"')
    SOURCE_FILE = Path(alt)
    if not SOURCE_FILE.exists():
        sys.exit(1)

DEST_DIR.mkdir(parents=True, exist_ok=True)

with open(SOURCE_FILE, "r", encoding="utf-8-sig", errors="ignore") as f:
    content = f.read()

pattern_focus = re.compile(r"(\bfocus\s*=\s*\{)", re.MULTILINE)
matches = list(pattern_focus.finditer(content))

focus_blocks = []
dependencies = {}

for match in matches:
    start_pos = match.start()
    brace_count = 0
    curr_pos = match.end() - 1
    end_pos = -1

    for idx in range(curr_pos, len(content)):
        c = content[idx]
        if c == '{':
            brace_count += 1
        elif c == '}':
            brace_count -= 1
            if brace_count == 0:
                end_pos = idx + 1
                break

    if end_pos != -1:
        block_text = content[start_pos:end_pos]
        id_match = re.search(r"\bid\s*=\s*([A-Za-z0-9_]+)", block_text)
        if id_match:
            f_id = id_match.group(1)
            focus_blocks.append((start_pos, end_pos, f_id, block_text))
            
            if "oppose_hitler" in f_id:
                ALT_ROOTS.add(f_id)

            prereq_blocks = re.findall(r"prerequisite\s*=\s*\{([^}]+)\}", block_text)
            parsed_prereqs = []
            for pb in prereq_blocks:
                reqs = re.findall(r"focus\s*=\s*([A-Za-z0-9_]+)", pb)
                if reqs:
                    parsed_prereqs.append(reqs)
            dependencies[f_id] = parsed_prereqs

excluded_focuses = set(ALT_ROOTS)
changed = True
while changed:
    changed = False
    for f_id, prereq_list in dependencies.items():
        if f_id in excluded_focuses:
            continue
        for req_or_group in prereq_list:
            if all(parent in excluded_focuses for parent in req_or_group):
                excluded_focuses.add(f_id)
                changed = True
                break

print(f"[*] Exclusive focus : {len(excluded_focuses)}")

def remove_subblock(text: str, block_name: str) -> str:
    pattern = re.compile(r"\b" + block_name + r"\s*=\s*\{")
    match = pattern.search(text)
    while match:
        start_pos = match.start()
        curr_pos = match.end() - 1
        brace_count = 0
        end_pos = -1
        for idx in range(curr_pos, len(text)):
            if text[idx] == '{':
                brace_count += 1
            elif text[idx] == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = idx + 1
                    break
        if end_pos != -1:
            text = text[:start_pos].rstrip(" \t") + text[end_pos:]
            match = pattern.search(text)
        else:
            break
    return text

locations_table = {}
items_table = {}
base_loc_id = 940000
base_item_id = 941000

new_content = []
last_pos = 0
kept_count = 0

for start_pos, end_pos, f_id, block_text in focus_blocks:
    new_content.append(content[last_pos:start_pos])
    last_pos = end_pos

    if f_id in excluded_focuses:
        new_content.append(block_text)
        continue

    kept_count += 1
    loc_id = base_loc_id + kept_count
    item_id = base_item_id + kept_count

    locations_table[f"FOCUS_{f_id}"] = loc_id
    items_table[item_id] = f"set_country_flag ap_key_{f_id}"
    block_text = remove_subblock(block_text, "mutually_exclusive")
    block_text = remove_subblock(block_text, "bypass")
    block_text = remove_subblock(block_text, "available")

    lock_block = f'\n\tavailable = {{\n\t\tcustom_trigger_tooltip = {{\n\t\t\ttooltip = "Requires the Archipelago Key : {f_id}"\n\t\t\thas_country_flag = ap_key_{f_id}\n\t\t}}\n\t}}'
    
    if re.search(r"(\bcost\s*=\s*\d+)", block_text):
        block_text = re.sub(r"(\bcost\s*=\s*\d+)", r"\1" + lock_block, block_text, count=1)
    else:
        block_text = re.sub(r"(\bid\s*=\s*[A-Za-z0-9_]+)", r"\1" + lock_block, block_text, count=1)

    check_code = f'\n\t\tlog = "AP_CHECK:FOCUS_{f_id}"'
    if re.search(r"\bcompletion_reward\s*=\s*\{", block_text):
        block_text = re.sub(r"(\bcompletion_reward\s*=\s*\{)", r"\1" + check_code, block_text, count=1)
    else:
        reward_block = f"\n\tcompletion_reward = {{{check_code}\n\t}}"
        block_text = block_text[:-1] + reward_block + "\n}"

    new_content.append(block_text)

new_content.append(content[last_pos:])

with open(DEST_FILE, "w", encoding="utf-8") as f :
    f.write("".join(new_content))

clean_rules = {}
for f_id in locations_table:
    raw_id = f_id.replace("FOCUS_", "")
    clean_rules[raw_id] = dependencies.get(raw_id, [])

mapping_data = {
    "locations": locations_table,
    "items": items_table,
    "tree_rules": clean_rules
}

with open(MAPPING_FILE, "w", encoding="utf-8") as f:
    json.dump(mapping_data, f, indent=4)

print(f"[+] Success: {kept_count} focuses configured")