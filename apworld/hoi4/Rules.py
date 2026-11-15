from typing import TYPE_CHECKING

from .Mapping import FOCUS_LOCATION_IDS, GOAL_FOCUSES, region_name

if TYPE_CHECKING:
    from . import HOI4World


def set_rules(world: "HOI4World") -> None:
    """Set the completion condition. Focus access rules live on region entrances."""
    player = world.player
    goal = world.options.victory_condition.current_key
    groups = [[region_name(f) for f in group if f in FOCUS_LOCATION_IDS] for group in GOAL_FOCUSES[goal]]

    world.multiworld.completion_condition[player] = lambda state: all(
        any(state.can_reach_region(r, player) for r in group) for group in groups
    )
