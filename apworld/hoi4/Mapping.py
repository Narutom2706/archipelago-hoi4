"""APWorld data loaded from mapping_germany.json (pkgutil works inside .apworld zips)."""
import json
import pkgutil

GAME = "Hearts of Iron IV"

_data = json.loads(pkgutil.get_data(__name__, "mapping_germany.json").decode("utf-8"))

# focus_id -> location id / item id
FOCUS_LOCATION_IDS = {k[len("FOCUS_"):]: int(v) for k, v in _data["locations"].items()}
FOCUS_ITEM_IDS = {
    cmd[len("set_country_flag ap_key_"):].split()[0]: int(item_id)
    for item_id, cmd in _data["items"].items()
    if cmd.startswith("set_country_flag ap_key_")
}
TREE_RULES: dict[str, list[list[str]]] = _data.get("tree_rules", {})

FOCUSES = [f for f in FOCUS_LOCATION_IDS if f in FOCUS_ITEM_IDS]


def location_name(focus_id: str) -> str:
    return f"Focus: {focus_id}"


def key_name(focus_id: str) -> str:
    return f"Key: {focus_id}"


def region_name(focus_id: str) -> str:
    return f"Focus region: {focus_id}"


# must match EXTRA_ITEMS in hoi4_archipelago_client.py
FILLER_ITEMS = {
    "Bonus: 150 Political Power": 949001,
    "Bonus: 8000 Infantry Equipment": 949002,
    "Bonus: 1000 Artillery": 949003,
}

LOCATION_NAME_TO_ID = {location_name(f): FOCUS_LOCATION_IDS[f] for f in FOCUSES}
KEY_NAME_TO_ID = {key_name(f): FOCUS_ITEM_IDS[f] for f in FOCUSES}
ITEM_NAME_TO_ID = {**KEY_NAME_TO_ID, **FILLER_ITEMS}

# focuses required for each goal (the goal itself is detected in game)
GOAL_FOCUSES = {
    "defeat_allies": [["GER_war_with_france", "GER_around_maginot"]],
    "defeat_ussr": [["GER_war_with_the_ussr"]],
    "world_conquest": [["GER_war_with_the_ussr"], ["GER_war_with_france", "GER_around_maginot"]],
}


def _check_consistency() -> None:
    problems = []
    for f in FOCUSES:
        for group in TREE_RULES.get(f, []):
            if not any(p in FOCUS_LOCATION_IDS for p in group):
                problems.append(f"{f} : prérequis {group} tous absents du mapping")
    for goal, groups in GOAL_FOCUSES.items():
        for group in groups:
            if not any(p in FOCUS_LOCATION_IDS for p in group):
                problems.append(f"objectif {goal} : aucun de {group} dans le mapping")
    if problems:
        raise Exception("mapping_germany.json incohérent :\n  " + "\n  ".join(problems))


_check_consistency()
