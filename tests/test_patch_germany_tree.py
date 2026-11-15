"""Run with: python -m pytest tests"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from ap_common import ParseError, parse, strip_comments
from patch_germany_tree import build, collect_focuses, focus_id, resync_effect, validate

SAMPLE = '''
focus_tree = {
	id = german_focus
	country = { factor = 0 modifier = { add = 10 tag = GER } }

	focus = {
		id = GER_root
		cost = 10
		x = 1
		y = 0
		completion_reward = {
			add_political_power = 120
		}
	}

	#focus = {
	#	id = GER_prioritize_the_army
	#	prerequisite = { focus = GER_root }
	#	cost = 10
	#	completion_reward = {
	#		random_core_state = {
	#	}
	#}

	focus = {
		id = GER_ascension_of_todt
		prerequisite = { focus = GER_root }
		cost = 2
		available = {
			has_government = fascism
			# ancien code : }
			if = {
				limit = { has_country_flag = GER_inner_circle_event_pending_flag }
				NOT = { has_country_flag = GER_inner_circle_event_pending_flag } # } {
			}
		}
		mutually_exclusive = { focus = GER_other }
		bypass = { has_completed_focus = GER_other }
		search_filters = { FOCUS_FILTER_POLITICAL }
		completion_reward = {
			custom_effect_tooltip = "MM: effects # are { underwhelming"
		}
	}

	focus = {
		id = GER_no_reward
		prerequisite = { focus = GER_root focus = GER_ascension_of_todt }
		cost = 5
	}

	focus = {
		id = GER_oppose_hitler
		prerequisite = { focus = GER_root }
		mutually_exclusive = { focus = GER_ascension_of_todt }
		cost = 10
	}

	focus = {
		id = GER_oppose_child
		prerequisite = { focus = GER_oppose_hitler }
		cost = 10
	}
}
'''


def test_strip_comments_keeps_hash_in_strings():
    out = strip_comments('a = "x # y" # comment\nb = 1 #}\n')
    assert out == 'a = "x # y"\nb = 1\n'


def test_parser_detects_unbalanced_braces():
    with pytest.raises(ParseError):
        parse("focus_tree = { focus = { id = A }")
    with pytest.raises(ParseError):
        parse("a = { } }")


def test_commented_focus_is_ignored():
    output, kept, deps, excluded = build(SAMPLE)
    assert "GER_prioritize_the_army" not in deps
    assert "GER_prioritize_the_army" not in output


def test_kept_and_excluded():
    output, kept, deps, excluded = build(SAMPLE)
    assert kept == ["GER_root", "GER_ascension_of_todt", "GER_no_reward"]
    assert {"GER_oppose_hitler", "GER_oppose_child"} <= excluded


def test_default_replaces_original_available():
    output, kept, _, _ = build(SAMPLE)
    validate(output, kept)
    todt = {focus_id(n): n for n in collect_focuses(parse(output))}["GER_ascension_of_todt"]
    avail = output[todt.first("available").open_pos:todt.first("available").close_pos]
    assert "has_country_flag = ap_key_GER_ascension_of_todt" in avail
    assert "has_government" not in avail
    assert todt.first("search_filters") is not None


def test_output_is_valid_and_original_available_kept():
    output, kept, _, _ = build(SAMPLE, keep_available=True)
    validate(output, kept)
    nodes = {focus_id(n): n for n in collect_focuses(parse(output))}

    todt = nodes["GER_ascension_of_todt"]
    avail = output[todt.first("available").open_pos:todt.first("available").close_pos]
    assert "has_country_flag = ap_key_GER_ascension_of_todt" in avail
    assert "has_government = fascism" in avail
    assert todt.first("search_filters") is not None
    assert not todt.find("mutually_exclusive") and not todt.find("bypass")
    reward = output[todt.first("completion_reward").open_pos:todt.first("completion_reward").close_pos]
    assert 'custom_effect_tooltip = "MM: effects # are { underwhelming"' in reward

    no_reward = nodes["GER_no_reward"]
    assert 'AP_CHECK:FOCUS_GER_no_reward' in output[no_reward.open_pos:no_reward.close_pos]

    oppose = nodes["GER_oppose_hitler"]
    assert not oppose.find("mutually_exclusive")
    assert "ap_key_GER_oppose_hitler" not in output


def test_resync_effect_parses():
    _, kept, _, _ = build(SAMPLE)
    root = parse(resync_effect(kept))
    assert len(root.first("ap_resync_checks").find("if")) == len(kept)


def test_technologies_lock_uses_allow_and_spares_ai():
    from patch_technologies import patch_file
    src = '''
@CR = 4
technologies = {
	infantry_weapons1 = {
		research_cost = 1
		start_year = 1936
		folder = { name = infantry_folder position = { x = 0 y = 0 } }
	}
	secret_tech = {
		start_year = 1940
		allow = { always = no } # cachée
	}
	no_year = { research_cost = 2 }
}
'''
    out, years = patch_file(src)
    assert years == {"1936", "1940"}
    root = parse(out)
    techs = root.first("technologies")
    assert "ap_tech_1936" in out[techs.first("infantry_weapons1").first("allow").open_pos:]
    secret = techs.first("secret_tech")
    assert len(secret.find("allow")) == 1
    assert "always = no" in out[secret.first("allow").open_pos:secret.first("allow").close_pos]
    assert "enable" not in out
    assert "NOT = { original_tag = GER }" in out


def test_excluded_focus_loses_exclusivity_with_kept_focus():
    src = SAMPLE.replace("mutually_exclusive = { focus = GER_ascension_of_todt }",
                         "mutually_exclusive = { focus = GER_ascension_of_todt focus = GER_oppose_child }")
    output, kept, _, _ = build(src)
    oppose = {focus_id(n): n for n in collect_focuses(parse(output))}["GER_oppose_hitler"]
    block = oppose.first("mutually_exclusive")
    assert [c.value for c in block.children] == ["GER_oppose_child"]  # links inside the free branch are kept
    output, _, _, _ = build(SAMPLE)
    oppose = {focus_id(n): n for n in collect_focuses(parse(output))}["GER_oppose_hitler"]
    assert oppose.first("mutually_exclusive") is None  # emptied block is removed


def test_key_events_and_localisation():
    from patch_germany_tree import key_events, key_localisation
    numbers = {"GER_root": 7, "GER_no_reward": 12}
    root = parse(key_events(numbers))
    ids = [e.first("id").value for e in root.find("country_event")]
    assert ids == ["ap_key.0", "ap_key.7", "ap_key.12"]
    assert all(e.first("option") is not None for e in root.find("country_event"))
    fr = key_localisation(numbers, "french")
    assert fr.startswith("l_french:\n")
    assert ' ap_key.7.d:0 "' in fr and "Unlocked focus: $GER_root$" in fr
    assert "§" not in fr
    assert "\\n\\n" in fr  # literal \n
    assert "Archipelago key received" in fr
    for line in fr.splitlines()[1:]:
        assert line.startswith(" ") and line.count('"') == 2


def test_player_mode_only_locks_mapped_focuses():
    output, kept, _, excluded = build(SAMPLE, restrict_to={"GER_root", "GER_no_reward"})
    assert kept == ["GER_root", "GER_no_reward"]
    assert "GER_ascension_of_todt" in excluded  # not in mapping -> free
    assert "ap_key_GER_ascension_of_todt" not in output
    validate(output, kept)


def test_alternative_roots_are_disabled():
    output, kept, _, excluded = build(SAMPLE)
    nodes = {focus_id(n): n for n in collect_focuses(parse(output))}
    root = nodes["GER_oppose_hitler"]
    avail = output[root.first("available").open_pos:root.first("available").close_pos]
    assert "always = no" in avail
    child = nodes["GER_oppose_child"]
    assert child.first("available") is None
    assert "GER_oppose_child" in excluded and "GER_oppose_child" not in kept
