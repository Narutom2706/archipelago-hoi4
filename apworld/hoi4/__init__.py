"""Hearts of Iron IV APWorld: the German focus tree, one key per focus.

A focus is reachable with its key and at least one reachable parent per prerequisite block.
Each focus is a region entered from a parent of its first block; other blocks are checked with
can_reach_region (registered as indirect conditions).
"""
from BaseClasses import Item, ItemClassification, Location, Region
from worlds.AutoWorld import World

from .Mapping import (FILLER_ITEMS, FOCUSES, GAME, ITEM_NAME_TO_ID, KEY_NAME_TO_ID, LOCATION_NAME_TO_ID,
                      TREE_RULES, FOCUS_LOCATION_IDS, key_name, location_name, region_name)
from .Options import HOI4Options
from .Rules import set_rules


class HOI4Item(Item):
    game = GAME


class HOI4Location(Location):
    game = GAME


class HOI4World(World):
    """Hearts of Iron IV: unlock Germany's focus tree key by key."""
    game = GAME
    options_dataclass = HOI4Options
    options: HOI4Options
    topology_present = True

    item_name_to_id = ITEM_NAME_TO_ID
    location_name_to_id = LOCATION_NAME_TO_ID
    item_name_groups = {"Keys": set(KEY_NAME_TO_ID), "Bonus": set(FILLER_ITEMS)}

    STARTING_KEYS = 2

    def create_item(self, name: str) -> Item:
        classification = ItemClassification.progression if name in KEY_NAME_TO_ID else ItemClassification.filler
        return HOI4Item(name, classification, self.item_name_to_id[name], self.player)

    def get_filler_item_name(self) -> str:
        return self.random.choice(sorted(FILLER_ITEMS))

    def _prereq_groups(self, focus: str) -> list[list[str]]:
        # ignore parents without a location (free branches)
        return [[p for p in group if p in FOCUS_LOCATION_IDS] for group in TREE_RULES.get(focus, [])]

    def create_regions(self) -> None:
        player = self.player
        menu = Region("Menu", player, self.multiworld)
        self.multiworld.regions.append(menu)

        regions = {}
        for focus in FOCUSES:
            region = Region(region_name(focus), player, self.multiworld)
            region.locations.append(HOI4Location(player, location_name(focus), LOCATION_NAME_TO_ID[location_name(focus)], region))
            regions[focus] = region
        self.multiworld.regions.extend(regions.values())

        for focus in FOCUSES:
            key = key_name(focus)
            groups = self._prereq_groups(focus)
            if not groups:
                menu.connect(regions[focus], f"Start -> {focus}", lambda state, k=key: state.has(k, player))
                continue

            first, others = groups[0], groups[1:]
            other_regions = [[region_name(p) for p in group] for group in others]

            def rule(state, k=key, groups_=other_regions):
                return state.has(k, player) and all(
                    any(state.can_reach_region(r, player) for r in group) for group in groups_)

            for parent in first:
                entrance = regions[parent].connect(regions[focus], f"{parent} -> {focus}", rule)
                for group in others:
                    for p in group:
                        self.multiworld.register_indirect_condition(regions[p], entrance)

    def create_items(self) -> None:
        roots = [f for f in FOCUSES if not self._prereq_groups(f)]
        starting = set(self.random.sample(roots, min(self.STARTING_KEYS, len(roots))))

        pool = []
        for focus in FOCUSES:
            item = self.create_item(key_name(focus))
            if focus in starting:
                self.multiworld.push_precollected(item)
            else:
                pool.append(item)

        # fill the remaining locations with bonus items
        missing = len(self.multiworld.get_unfilled_locations(self.player)) - len(pool)
        pool += [self.create_item(self.get_filler_item_name()) for _ in range(missing)]
        self.multiworld.itempool += pool

    def set_rules(self) -> None:
        set_rules(self)

    def fill_slot_data(self) -> dict:
        return {"goal": self.options.victory_condition.current_key}
