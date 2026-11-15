"""Generate the Archipelago mod from the installed germany.txt.

Each kept focus gets an ap_key_<focus> flag requirement and logs AP_CHECK on completion.
Existing IDs in mapping_germany.json are preserved.

Usage: python patch_germany_tree.py [--keep-available] [path\\to\\germany.txt]

By default the original 'available' condition is replaced by the key, so the APWorld logic
(keys + prerequisites) is exact. --keep-available keeps the game conditions as well.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from ap_common import (ParseError, Node, apply_edits, ask_hoi4_install, find_hoi4_install, mod_dir,
                       parse, pe_timestamp, resource_path, statement_span, strip_comments)

TAG = "GER"
GENERATOR_VERSION = 5          # bump when the generated output changes
MAPPING_FILE = resource_path("mapping_germany.json")
BUILD_MARKER = ".ap_build.json"
BASE_LOC_ID = 940000
BASE_ITEM_ID = 941000
KEEP_ORIGINAL_AVAILABLE = False  # --keep-available overrides

# mapping_germany.json is copied into these APWorld folders
APWORLD_DIRS = [Path(r"C:\ProgramData\Archipelago\lib\worlds\hoi4"),
                Path(__file__).parent / "apworld" / "hoi4"]

# Alternative branch roots: no key, no check
ALT_ROOTS = {
    "GER_oppose_hitler",
    "GER_oppose_hitler_ww",
    "GER_prioritize_economic_growth",
}
# Roots made unavailable, which blocks their whole branch
DISABLED_ROOTS = set(ALT_ROOTS)

GOALS_FILE = """on_actions = {
\ton_capitulation = {
\t\teffect = {
\t\t\tif = {
\t\t\t\tlimit = {
\t\t\t\t\toriginal_tag = ENG
\t\t\t\t\tGER = { has_war_with = ROOT }
\t\t\t\t}
\t\t\t\tlog = "AP_GOAL:defeat_allies"
\t\t\t}
\t\t\tif = {
\t\t\t\tlimit = {
\t\t\t\t\toriginal_tag = SOV
\t\t\t\t\tGER = { has_war_with = ROOT }
\t\t\t\t}
\t\t\t\tlog = "AP_GOAL:defeat_ussr"
\t\t\t}
\t\t\tif = {
\t\t\t\tlimit = {
\t\t\t\t\tENG = { has_capitulated = yes }
\t\t\t\t\tSOV = { has_capitulated = yes }
\t\t\t\t\tUSA = { has_capitulated = yes }
\t\t\t\t}
\t\t\t\tlog = "AP_GOAL:world_conquest"
\t\t\t}
\t\t}
\t}

\t# Re-log completed focuses on game start/load
\ton_startup = {
\t\teffect = {
\t\t\tGER = { ap_resync_checks = yes }
\t\t}
\t}
}
"""

EVENTS_FILE = """add_namespace = ap

# Re-logs completed focuses (console: event ap.1 GER)
country_event = {
\tid = ap.1
\thidden = yes
\tis_triggered_only = yes
\timmediate = {
\t\tap_resync_checks = yes
\t}
\toption = {
\t\tname = ap.1.a
\t}
}
"""


def lock_block(f_id: str) -> str:
    return (f'\n\t\tcustom_trigger_tooltip = {{\n'
            f'\t\t\ttooltip = "Requires the Archipelago key: {f_id}"\n'
            f'\t\t\thas_country_flag = ap_key_{f_id}\n'
            f'\t\t}}')


def check_line(f_id: str) -> str:
    return f'\n\t\tlog = "AP_CHECK:FOCUS_{f_id}"'


def focus_id(node: Node) -> str | None:
    id_node = node.first("id")
    return id_node.value if id_node else None


def collect_focuses(root: Node) -> list[Node]:
    focuses = []
    for tree in root.find("focus_tree"):
        focuses.extend(c for c in tree.find("focus") if c.is_block)
    return focuses


def prerequisites(node: Node) -> list[list[str]]:
    groups = []
    for pre in node.find("prerequisite"):
        reqs = [c.value for c in pre.find("focus") if c.value]
        if reqs:
            groups.append(reqs)
    return groups


def compute_excluded(deps: dict[str, list[list[str]]]) -> set[str]:
    excluded = {f for f in deps if f in ALT_ROOTS or "oppose_hitler" in f}
    changed = True
    while changed:
        changed = False
        for f_id, groups in deps.items():
            if f_id in excluded:
                continue
            # focuses inside a prerequisite block are OR, separate blocks are AND
            if any(all(p in excluded for p in group) for group in groups):
                excluded.add(f_id)
                changed = True
    return excluded


def patch_focus(text: str, node: Node, f_id: str, keep_available: bool) -> list[tuple[int, int, str]]:
    edits = []

    # drop mutual exclusions and bypasses
    for key in ("mutually_exclusive", "bypass"):
        for child in node.find(key):
            s, e = statement_span(text, child)
            edits.append((s, e, ""))

    # key lock replaces the original condition (or is added to it with keep_available)
    available = node.first("available")
    if available and available.is_block and keep_available:
        edits.append((available.open_pos + 1, available.open_pos + 1, lock_block(f_id)))
    elif available and available.is_block:
        edits.append((available.open_pos + 1, available.close_pos, lock_block(f_id) + "\n\t"))
    else:
        anchor = node.first("id")
        edits.append((anchor.end, anchor.end, f"\n\tavailable = {{{lock_block(f_id)}\n\t}}"))

    # send the check on completion
    reward = node.first("completion_reward")
    if reward and reward.is_block:
        edits.append((reward.open_pos + 1, reward.open_pos + 1, check_line(f_id)))
    else:
        edits.append((node.close_pos, node.close_pos,
                      f"\tcompletion_reward = {{{check_line(f_id)}\n\t}}\n\t"))
    return edits


def load_previous_mapping() -> tuple[dict[str, int], dict[str, int]]:
    """Return {focus_id: number} for already assigned locations and items."""
    if not MAPPING_FILE.exists():
        return {}, {}
    data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    locs = {}
    for name, loc_id in data.get("locations", {}).items():
        if name.startswith("FOCUS_"):
            locs[name[len("FOCUS_"):]] = int(loc_id) - BASE_LOC_ID
    items = {}
    prefix = "set_country_flag ap_key_"
    for item_id, cmd in data.get("items", {}).items():
        if cmd.startswith(prefix):
            items[cmd[len(prefix):].split()[0]] = int(item_id) - BASE_ITEM_ID
    return locs, items


def validate(output: str, kept: list[str]) -> None:
    root = parse(output)  # raises ParseError on unbalanced braces
    by_id = {focus_id(n): n for n in collect_focuses(root)}
    problems = []
    for f_id in kept:
        node = by_id.get(f_id)
        if node is None:
            problems.append(f"{f_id} : introuvable dans la sortie")
            continue
        avail = node.find("available")
        if len(avail) != 1:
            problems.append(f"{f_id} : {len(avail)} blocs available")
        elif f"ap_key_{f_id}" not in output[avail[0].open_pos:avail[0].close_pos]:
            problems.append(f"{f_id} : clé absente du available")
        rewards = node.find("completion_reward")
        if len(rewards) != 1 or f"AP_CHECK:FOCUS_{f_id}" not in output[rewards[0].open_pos:rewards[0].close_pos]:
            problems.append(f"{f_id} : check absent de completion_reward")
        if node.find("mutually_exclusive") or node.find("bypass"):
            problems.append(f"{f_id} : mutually_exclusive/bypass restant")
    if problems:
        raise SystemExit("[!] Sortie invalide :\n  " + "\n  ".join(problems[:20]))


def build(source_text: str, keep_available: bool = KEEP_ORIGINAL_AVAILABLE, restrict_to: set[str] | None = None
          ) -> tuple[str, list[str], dict[str, list[list[str]]], set[str]]:
    """restrict_to: only lock these focuses; others stay free (no key, no check)."""
    clean = strip_comments(source_text)
    root = parse(clean)
    focuses = collect_focuses(root)
    deps = {}
    for node in focuses:
        f_id = focus_id(node)
        if f_id:
            deps[f_id] = prerequisites(node)
    excluded = compute_excluded(deps)
    if restrict_to is not None:
        excluded |= {f for f in deps if f not in restrict_to}

    kept = [focus_id(n) for n in focuses if focus_id(n) and focus_id(n) not in excluded]
    kept_set = set(kept)
    edits = []
    for node in focuses:
        f_id = focus_id(node)
        if not f_id:
            continue
        if f_id in excluded:
            edits.extend(unlink_exclusions(clean, node, kept_set))
            if f_id in DISABLED_ROOTS:
                edits.extend(disable_focus(node))
        else:
            edits.extend(patch_focus(clean, node, f_id, keep_available))
    return apply_edits(clean, edits), kept, deps, excluded


def disable_focus(node: Node) -> list[tuple[int, int, str]]:
    """Make a focus (and therefore its branch) permanently unavailable."""
    lock = ('\n\t\tcustom_trigger_tooltip = {\n\t\t\ttooltip = "Disabled in Archipelago"\n'
            '\t\t\talways = no\n\t\t}')
    available = node.first("available")
    if available and available.is_block:
        return [(available.open_pos + 1, available.close_pos, lock + "\n\t")]
    anchor = node.first("id")
    return [(anchor.end, anchor.end, f"\n\tavailable = {{{lock}\n\t}}")]


def unlink_exclusions(text: str, node: Node, kept: set[str]) -> list[tuple[int, int, str]]:
    """Remove references to locked focuses from mutually_exclusive blocks of free focuses."""
    edits = []
    for block in node.find("mutually_exclusive"):
        entries = [c for c in block.children if c.key == "focus"]
        to_remove = [c for c in entries if c.value in kept]
        if not to_remove:
            continue
        if len(to_remove) == len(block.children):
            edits.append((*statement_span(text, block), ""))
        else:
            edits.extend((c.start, c.end, "") for c in to_remove)
    return edits


def resync_effect(kept: list[str]) -> str:
    lines = ["ap_resync_checks = {"]
    for f_id in kept:
        lines.append(f'\tif = {{ limit = {{ has_completed_focus = {f_id} }} '
                     f'log = "AP_CHECK:FOCUS_{f_id}" }}')
    lines.append("}")
    return "\n".join(lines) + "\n"


# --- Key notifications ---
LANGUAGES = ["english", "french", "german", "spanish", "braz_por", "russian",
             "polish", "japanese", "simp_chinese", "korean"]
KEY_TEXTS = {
    # English for every language; $FOCUS$ resolves to the localized focus name
    "title": "Archipelago key received",
    "ok": "Onward!",
    "one": "A new Archipelago key has arrived.\\n\\nUnlocked focus: $FOCUS$\\n\\n"
           "You can take it as soon as its prerequisites are met.",
    "many": "Several Archipelago keys arrived at once.\\n\\nCheck your focus tree: "
            "new focuses are unlocked.",
}


def key_events(numbers: dict[str, int]) -> str:
    """ap_key.0: several keys at once. ap_key.<n>: key for item 941000 + n."""
    def event(event_id: str, desc: str) -> str:
        return (f"country_event = {{\n\tid = {event_id}\n\ttitle = ap_key_title\n\tdesc = {desc}\n"
                f"\tis_triggered_only = yes\n\toption = {{ name = ap_key_ok }}\n}}\n")
    parts = ["add_namespace = ap_key\n", event("ap_key.0", "ap_key.0.d")]
    parts += [event(f"ap_key.{n}", f"ap_key.{n}.d") for n in numbers.values()]
    return "\n".join(parts)


def key_localisation(numbers: dict[str, int], language: str) -> str:
    t = KEY_TEXTS
    lines = [f"l_{language}:",
             f' ap_key_title:0 "{t["title"]}"',
             f' ap_key_ok:0 "{t["ok"]}"',
             f' ap_key.0.d:0 "{t["many"]}"']
    lines += [f' ap_key.{n}.d:0 "{t["one"].replace("FOCUS", f_id)}"' for f_id, n in numbers.items()]
    return "\n".join(lines) + "\n"


# --- Generation ---
def source_file(explicit: Path | None = None) -> Path:
    if explicit:
        return explicit
    install = find_hoi4_install() or ask_hoi4_install()
    return install / "common" / "national_focus" / "germany.txt"


def build_marker(source: Path) -> dict:
    exe = source.parents[2] / "hoi4.exe"
    mapping_size = MAPPING_FILE.stat().st_size if MAPPING_FILE.exists() else 0
    return {"generator": GENERATOR_VERSION, "game_exe": pe_timestamp(exe),
            "source_size": source.stat().st_size, "mapping_size": mapping_size}


def mod_is_up_to_date(source: Path | None = None) -> bool:
    try:
        source = source_file(source)
        marker = json.loads((mod_dir() / BUILD_MARKER).read_text(encoding="utf-8"))
        return marker == build_marker(source)
    except (OSError, ValueError):
        return False


def generate(source: Path | None = None, keep_available: bool = KEEP_ORIGINAL_AVAILABLE,
             player_mode: bool = False, log=print) -> None:
    """player_mode: use the bundled mapping as-is and only write the mod folder."""
    source = source_file(source)
    if not source.exists():
        raise SystemExit(f"[!] Fichier introuvable : {source}")
    log(f"[*] Source : {source}")

    prev_locs, prev_items = load_previous_mapping()
    restrict = (set(prev_locs) | set(prev_items)) if player_mode else None
    if player_mode and not restrict:
        raise SystemExit("[!] mapping_germany.json introuvable : la release est incomplète.")

    text = source.read_text(encoding="utf-8-sig", errors="ignore")
    try:
        output, kept, deps, excluded = build(text, keep_available, restrict)
    except ParseError as e:
        raise SystemExit(f"[!] Impossible de parser {source.name} : {e}")
    validate(output, kept)

    # keep existing IDs, assign new ones after the highest
    used = set(prev_locs.values()) | set(prev_items.values())
    next_n = max(used, default=0) + 1
    numbers, new_ones = {}, []
    for f_id in kept:
        n = prev_locs.get(f_id) or prev_items.get(f_id)
        if n is None:
            n = next_n
            next_n += 1
            new_ones.append(f_id)
        numbers[f_id] = n
    removed = sorted(set(prev_locs) - set(kept))

    target = mod_dir()
    dirs = {name: target / name for name in ("common/national_focus", "common/on_actions",
                                             "common/scripted_effects", "events")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    (dirs["common/national_focus"] / "germany.txt").write_text(output, encoding="utf-8")
    (dirs["common/on_actions"] / "ap_goals.txt").write_text(GOALS_FILE, encoding="utf-8")
    (dirs["common/scripted_effects"] / "ap_scripted_effects.txt").write_text(resync_effect(kept), encoding="utf-8")
    (dirs["events"] / "ap_events.txt").write_text(EVENTS_FILE, encoding="utf-8")
    (dirs["events"] / "ap_key_events.txt").write_text(key_events(numbers), encoding="utf-8")
    for language in LANGUAGES:
        loc_dir = target / "localisation" / language
        loc_dir.mkdir(parents=True, exist_ok=True)
        # localisation files must be UTF-8 with BOM
        (loc_dir / f"ap_keys_l_{language}.yml").write_text(key_localisation(numbers, language),
                                                          encoding="utf-8-sig")
    stale = dirs["common/on_actions"] / "archipelago_actions.txt"
    if stale.exists():
        stale.unlink()

    if not player_mode:
        mapping = {
            "locations": {f"FOCUS_{f}": BASE_LOC_ID + n for f, n in numbers.items()},
            "items": {str(BASE_ITEM_ID + n): f"set_country_flag ap_key_{f}" for f, n in numbers.items()},
            "tree_rules": {f_id: deps.get(f_id, []) for f_id in kept},
        }
        MAPPING_FILE.write_text(json.dumps(mapping, indent=4), encoding="utf-8")
        for apworld in APWORLD_DIRS:
            if apworld.is_dir():
                shutil.copyfile(MAPPING_FILE, apworld / MAPPING_FILE.name)
                log(f"[*] mapping recopié dans l'APWorld : {apworld}")

    (target / BUILD_MARKER).write_text(json.dumps(build_marker(source)), encoding="utf-8")

    mode = "conditions d'origine conservées" if keep_available else "conditions d'origine remplacées par la clé"
    log(f"[+] {len(kept)} focus verrouillés ({mode}), {len(excluded)} laissés libres. Mod écrit dans {target}")
    if new_ones:
        log(f"[!] {len(new_ones)} nouveaux focus (nouveaux IDs) : {', '.join(new_ones[:10])}"
            f"{' ...' if len(new_ones) > 10 else ''}")
    if removed:
        label = "absents de ta version du jeu" if player_mode else "retirés du mapping"
        log(f"[!] {len(removed)} focus {label} : {', '.join(removed[:10])}{' ...' if len(removed) > 10 else ''}")
        if player_mode:
            log("    -> ces checks seront impossibles : il faut une release du mod à jour pour ta version de HOI4.")
    if (new_ones or removed) and not player_mode:
        log("    -> pense à mettre à jour les items/locations de ton APWorld.")


def main() -> None:
    args = [a for a in sys.argv[1:] if a != "--keep-available"]
    generate(Path(args[0].strip('"')) if args else None,
             keep_available=KEEP_ORIGINAL_AVAILABLE or "--keep-available" in sys.argv[1:])


if __name__ == "__main__":
    main()
