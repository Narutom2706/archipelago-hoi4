"""Local mock Archipelago server for client testing (ws://localhost:38281).

Each check returns the item with the same number.
"""
import asyncio
import json
from pathlib import Path

import websockets

PORT = 38281
LOC_BASE, ITEM_BASE = 940000, 941000

# sent on connect, like a real start inventory
STARTING_FOCUSES = ["GER_the_four_year_plan", "GER_remilitarize_the_rhineland"]


def starting_items() -> list[int]:
    mapping = json.loads((Path(__file__).parent / "mapping_germany.json").read_text(encoding="utf-8"))
    by_focus = {cmd.split("ap_key_", 1)[1]: int(i) for i, cmd in mapping["items"].items() if "ap_key_" in cmd}
    return [by_focus[f] for f in STARTING_FOCUSES if f in by_focus]


checked: set[int] = set()
given: list[int] = starting_items()


def item_packets(index: int) -> list[dict]:
    return [{"cmd": "ReceivedItems", "index": index,
             "items": [{"item": i, "location": 0, "player": 1, "flags": 1} for i in given[index:]]}]


async def handler(ws):
    print("[SERVEUR] Client connecté")
    await ws.send(json.dumps([{"cmd": "RoomInfo", "seed_name": "mock_seed",
                               "version": {"major": 0, "minor": 5, "build": 0, "class": "Version"}}]))
    async for message in ws:
        for pkt in json.loads(message):
            cmd = pkt.get("cmd")
            if cmd == "Connect":
                print(f"[SERVEUR] Joueur : {pkt.get('name')}")
                await ws.send(json.dumps([{"cmd": "Connected", "slot": 1, "checked_locations": sorted(checked),
                                           "slot_data": {"goal": "defeat_allies"}}]))
                await ws.send(json.dumps(item_packets(0)))
            elif cmd == "Sync":
                await ws.send(json.dumps(item_packets(0)))
            elif cmd == "LocationChecks":
                for loc in pkt.get("locations", []):
                    if loc in checked:
                        continue
                    checked.add(loc)
                    index = len(given)
                    given.append(ITEM_BASE + (loc - LOC_BASE))
                    print(f"[SERVEUR] Check {loc} -> item {given[-1]}")
                    await ws.send(json.dumps(item_packets(index)))
            elif cmd == "StatusUpdate" and pkt.get("status") == 30:
                print("\n" + "=" * 40 + "\n  VICTOIRE REÇUE PAR LE SERVEUR\n" + "=" * 40 + "\n")


async def main():
    print(f"[SERVEUR AP] ws://localhost:{PORT} ... en attente du client ({len(given)} clés de départ)")
    async with websockets.serve(handler, "localhost", PORT):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
