"""Player entry point (built as HOI4_Archipelago.exe).

Generates or updates the mod when needed, then runs the client.
"""
from __future__ import annotations

import sys

from ap_common import MOD_NAME, mod_dir

MOD_FILE_TEMPLATE = '''version="{version}"
tags={{
\t"Gameplay"
}}
name="{name}"
supported_version="1.19.*"
'''
MOD_VERSION = "0.2.0"


def ensure_descriptor() -> bool:
    """Create descriptor.mod and Archipelago.mod if missing. Return True if created."""
    target = mod_dir()
    target.mkdir(parents=True, exist_ok=True)
    created = False
    descriptor = target / "descriptor.mod"
    if not descriptor.exists():
        descriptor.write_text(MOD_FILE_TEMPLATE.format(version=MOD_VERSION, name=MOD_NAME), encoding="utf-8")
        created = True
    launcher_file = target.parent / f"{MOD_NAME}.mod"
    if not launcher_file.exists():
        content = MOD_FILE_TEMPLATE.format(version=MOD_VERSION, name=MOD_NAME)
        content += f'path="{target.as_posix()}"\n'
        launcher_file.write_text(content, encoding="utf-8")
        created = True
    return created


def ensure_mod() -> None:
    import patch_germany_tree as gen

    created = ensure_descriptor()
    if gen.mod_is_up_to_date():
        print("[+] Mod Archipelago à jour.")
    else:
        print("[*] Génération du mod pour ta version de HOI4...")
        gen.generate(player_mode=True)
    if created:
        print("\n[!] Première installation : ouvre le launcher Paradox, va dans « Playsets » et active le\n"
              "    mod « Archipelago ». À faire une seule fois.\n")


def main() -> None:
    print("=== Hearts of Iron IV x Archipelago ===\n")
    try:
        ensure_mod()
    except SystemExit as e:
        print(e)
        input("Entrée pour quitter...")
        sys.exit(1)

    import hoi4_archipelago_client as client
    try:
        client.main()
    finally:
        if getattr(sys, "frozen", False):
            input("\nEntrée pour fermer la fenêtre...")


if __name__ == "__main__":
    main()
