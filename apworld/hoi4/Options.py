from dataclasses import dataclass

from Options import Choice, PerGameCommonOptions


class VictoryCondition(Choice):
    """Goal required to complete the run."""
    display_name = "Victory Condition"
    option_defeat_allies = 0
    option_defeat_ussr = 1
    option_world_conquest = 2  # ENG + SOV + USA
    default = 0


@dataclass
class HOI4Options(PerGameCommonOptions):
    victory_condition: VictoryCondition
