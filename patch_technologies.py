"""Lock German technologies by year tier (flag ap_tech_<year>).

Not deployed: no AP item grants these flags yet.

Usage: python patch_technologies.py
"""
from __future__ import annotations

import sys

from ap_common import ParseError, apply_edits, ask_hoi4_install, mod_dir, parse, strip_comments

TAG = "GER"


def lock(year: str) -> str:
    # only Germany is locked
    return (f'\n\t\tcustom_trigger_tooltip = {{\n'
            f'\t\t\ttooltip = "Requires the Archipelago tier {year}"\n'
            f'\t\t\tOR = {{\n'
            f'\t\t\t\tNOT = {{ original_tag = {TAG} }}\n'
            f'\t\t\t\thas_country_flag = ap_tech_{year}\n'
            f'\t\t\t}}\n'
            f'\t\t}}')


def patch_file(text: str) -> tuple[str, set[str]]:
    clean = strip_comments(text)
    root = parse(clean)
    edits, years = [], set()
    for block in root.find("technologies"):
        for tech in block.children:
            if not tech.is_block or tech.key is None:
                continue
            start_year = tech.first("start_year")
            if not start_year or not (start_year.value or "").isdigit():
                continue
            year = start_year.value
            years.add(year)
            allow = tech.first("allow")
            if allow and allow.is_block:
                edits.append((allow.open_pos + 1, allow.open_pos + 1, lock(year)))
            else:
                edits.append((start_year.end, start_year.end, f"\n\tallow = {{{lock(year)}\n\t}}"))
    return apply_edits(clean, edits), years


def main() -> None:
    source_dir = ask_hoi4_install() / "common" / "technologies"
    target_dir = mod_dir() / "common" / "technologies"
    target_dir.mkdir(parents=True, exist_ok=True)

    all_years: set[str] = set()
    count = 0
    for file in sorted(source_dir.glob("*.txt")):
        text = file.read_text(encoding="utf-8-sig", errors="ignore")
        try:
            new_text, years = patch_file(text)
        except ParseError as e:
            print(f"[!] {file.name} ignoré : {e}")
            continue
        if years:
            (target_dir / file.name).write_text(new_text, encoding="utf-8")
            all_years |= years
            count += 1

    print(f"[+] {count} fichiers de technologies générés dans {target_dir}")
    print(f"[!] Flags nécessaires (à donner via des items AP) : "
          f"{', '.join(f'ap_tech_{y}' for y in sorted(all_years))}")


if __name__ == "__main__":
    sys.exit(main())
