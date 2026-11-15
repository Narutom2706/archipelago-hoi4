"""Package apworld/hoi4 as hoi4.apworld.

    python tools/build_apworld.py [output.apworld]
"""
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORLD = ROOT / "apworld" / "hoi4"
MAPPING = ROOT / "mapping_germany.json"

# APContainer version used by Archipelago 0.6.x (worlds/Files.py)
CONTAINER_VERSION = 7


def build(output: Path) -> None:
    manifest = json.loads((WORLD / "archipelago.json").read_text(encoding="utf-8"))
    manifest.update({"version": CONTAINER_VERSION, "compatible_version": CONTAINER_VERSION})

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(WORLD.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.name in ("archipelago.json", MAPPING.name):
                continue
            zf.write(path, f"hoi4/{path.relative_to(WORLD).as_posix()}")
        # the repo root mapping is the reference
        zf.write(MAPPING, f"hoi4/{MAPPING.name}")
        zf.writestr("hoi4/archipelago.json", json.dumps(manifest, indent=4))
    print(f"[+] {output} ({output.stat().st_size // 1024} Ko)")


if __name__ == "__main__":
    build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "hoi4.apworld")
