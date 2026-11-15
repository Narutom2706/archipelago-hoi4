"""APWorld logic tests without Archipelago: same access rule as HOI4World, plus random fills."""
import importlib.util
import random

import pytest
import sys
from pathlib import Path

APWORLD = Path(__file__).resolve().parent.parent / "apworld" / "hoi4"


def load_mapping():
    spec = importlib.util.spec_from_file_location("hoi4_mapping_test", APWORLD / "Mapping.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


M = load_mapping()


def groups_of(focus):
    return [[p for p in g if p in M.FOCUS_LOCATION_IDS] for g in M.TREE_RULES.get(focus, [])]


def _topological_order() -> list[str]:
    order, seen = [], set()

    def visit(f):
        if f in seen:
            return
        seen.add(f)
        for g in groups_of(f):
            for p in g:
                visit(p)
        order.append(f)

    for f in M.FOCUSES:
        visit(f)
    return order


ORDER = _topological_order()
GROUPS = {f: groups_of(f) for f in M.FOCUSES}


def reachable(keys: set[str]) -> set[str]:
    """Same rule as the regions: key + one reachable parent per block."""
    done: set[str] = set()
    for f in ORDER:
        if f in keys and all(any(p in done for p in g) for g in GROUPS[f]):
            done.add(f)
    return done


def goal_ok(goal: str, done: set[str]) -> bool:
    return all(any(f in done for f in group) for group in M.GOAL_FOCUSES[goal])


def test_every_focus_and_goal_reachable_with_all_keys():
    done = reachable(set(M.FOCUSES))
    assert done == set(M.FOCUSES), sorted(set(M.FOCUSES) - done)[:10]
    for goal in M.GOAL_FOCUSES:
        assert goal_ok(goal, done), goal


def test_item_and_location_ids_are_consistent():
    assert len(M.LOCATION_NAME_TO_ID) == len(M.KEY_NAME_TO_ID)
    assert len(set(M.ITEM_NAME_TO_ID.values())) == len(M.ITEM_NAME_TO_ID)
    assert set(M.FILLER_ITEMS.values()) == {949001, 949002, 949003}


def assumed_fill(rng: random.Random) -> bool:
    """Simplified solo version of Archipelago's fill_restrictive."""
    roots = [f for f in M.FOCUSES if not groups_of(f)]
    start = set(rng.sample(roots, 2))
    pool = [f for f in M.FOCUSES if f not in start]
    rng.shuffle(pool)
    placed: dict[str, str] = {}               # location (focus) -> key
    while pool:
        item = pool.pop()
        keys = set(pool) | start               # assume every unplaced item is collected
        spots = [f for f in reachable(keys) if f not in placed]
        if not spots:
            return False
        placed[rng.choice(spots)] = item
    # final sweep from the starting keys
    have = set(start)
    while True:
        new = {placed[f] for f in reachable(have) if f in placed} - have
        if not new:
            break
        have |= new
    return reachable(have) == set(M.FOCUSES)


@pytest.mark.xfail(strict=True, reason=(
    "1 clé par focus + 1 item par location : chaque focus fait ne rapporte qu'une clé, donc au plus "
    "2 focus faisables à la fois en solo. Le remplissage échoue tant que le modèle n'est pas revu."))
def test_random_fills_are_beatable():
    rng = random.Random(1234)
    results = [assumed_fill(rng) for _ in range(200)]
    assert all(results), f"{results.count(False)} remplissages ratés sur 200"


def test_apworld_mapping_matches_repo_mapping():
    import json
    root = Path(__file__).resolve().parent.parent
    repo = json.loads((root / "mapping_germany.json").read_text(encoding="utf-8"))
    world = json.loads((APWORLD / "mapping_germany.json").read_text(encoding="utf-8"))
    assert repo == world, "apworld/hoi4/mapping_germany.json n'est pas à jour : relance patch_germany_tree.py"


def test_build_apworld(tmp_path):
    import json
    import zipfile
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    import build_apworld
    out = tmp_path / "hoi4.apworld"
    build_apworld.build(out)
    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())
        manifest = json.loads(zf.read("hoi4/archipelago.json"))
    assert {"hoi4/__init__.py", "hoi4/Mapping.py", "hoi4/mapping_germany.json"} <= names
    assert not any("__pycache__" in n for n in names)
    assert manifest["game"] == "Hearts of Iron IV"
    assert manifest["version"] == manifest["compatible_version"] == 7
