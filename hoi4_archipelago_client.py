import asyncio
import json
import os
import re
import time
from pathlib import Path
import websockets
from pynput.keyboard import Controller, Key

DOCS_HOI4 = Path(os.path.expanduser("~")) / "Documents" / "Paradox Interactive" / "Hearts of Iron IV"
GAME_LOG_PATH = DOCS_HOI4 / "logs" / "game.log"
MAPPING_FILE = Path(__file__).parent / "mapping_germany.json"
PIPE_NAME = r"\\.\pipe\hoi4_ap"

default_server = "localhost:38281"
server_input = input(f"Address of the AP server [{default_server}] : ").strip() or default_server
if not server_input.startswith("ws://") and not server_input.startswith("wss://"):
    SERVER_URI = f"ws://{server_input}"
else:
    SERVER_URI = server_input

default_slot = "Allemagne"
SLOT_NAME = input(f"Name of the player / Slot [{default_slot}] : ").strip() or default_slot
GAME_NAME = "Hearts of Iron IV"

print(f"[*] Target : {SERVER_URI} | Slot : {SLOT_NAME}")

SELECTED_GOAL = "defeat_allies"

if MAPPING_FILE.exists():
    with open(MAPPING_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
        LOCATION_TABLE = data.get("locations", {})
        ITEM_TABLE = {int(k): v for k, v in data.get("items", {}).items()}
    print(f"[+] {len(LOCATION_TABLE)} checks et {len(ITEM_TABLE)} items chargés.")
else:
    print("[!] Warning: mapping_germany.json not found.")
    LOCATION_TABLE = {}
    ITEM_TABLE = {}

EXTRA_ITEMS = {
    949001: "pp 150",
    949002: "add_equipment 8000 infantry_equipment_1",
    949003: "add_equipment 1500 artillery_equipment_1",
}
ITEM_TABLE.update(EXTRA_ITEMS)

RE_CHECK = re.compile(r"AP_CHECK:([A-Za-z0-9_]+)")
RE_GOAL = re.compile(r"AP_GOAL:([A-Za-z0-9_]+)")

sent_locations = set()

def arm_console_hook():
    print("[*] Automatic arming of the console hook...")
    kb = Controller()
    time.sleep(0.5)
    kb.press('²')
    kb.release('²')
    time.sleep(0.15)
    kb.press(Key.backspace)
    kb.release(Key.backspace)
    time.sleep(0.05)
    kb.type("init")
    time.sleep(0.05)
    kb.press(Key.enter)
    kb.release(Key.enter)
    time.sleep(0.1)
    kb.press('²')
    kb.release('²')
    print("[+] Console armed and ready")

def send_pipe_command(command: str) -> bool:
    try:
        with open(PIPE_NAME, "wb", buffering=0) as pipe:
            pipe.write(command.encode("utf-8"))
        return True
    except FileNotFoundError:
        print("[!] Warning: The Named Pipe is unavailable. Is the DLL injected?")
        return False
    except Exception as e:
        print(f"[!] Pipe Error : {e}")
        return False

def execute_hoi4_batch(commands: list[str]):
    for cmd in commands:
        if send_pipe_command(cmd):
            print(f"[PIPE -> HOI4] Executed : {cmd}")
        time.sleep(0.02)

async def watch_hoi4_logs(ws):
    print("[+] Active game.log monitoring...")
    with open(GAME_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if not line:
                f.seek(f.tell())
                await asyncio.sleep(0.1)
                continue
            match_check = RE_CHECK.search(line)
            if match_check:
                raw_id = match_check.group(1)
                if raw_id in LOCATION_TABLE:
                    loc_id = LOCATION_TABLE[raw_id]
                    if loc_id not in sent_locations:
                        sent_locations.add(loc_id)
                        print(f"\n[CHECK] {raw_id} (ID: {loc_id}) completed -> Sending to server !")
                        packet = [{"cmd": "LocationChecks", "locations": [loc_id]}]
                        await ws.send(json.dumps(packet))
                else:
                    print(f"\n[UNKNOWN CHECK] Received '{raw_id}', not listed in LOCATION_TABLE.")

            match_goal = RE_GOAL.search(line)
            if match_goal:
                goal_id = match_goal.group(1)
                if SELECTED_GOAL == "any" or goal_id == SELECTED_GOAL:
                    print(f"\n[VICTORY ACHIEVED] Goal validated ({goal_id}) -> Sending to multiworld !")
                    packet = [{"cmd": "StatusUpdate", "status": 30}]
                    await ws.send(json.dumps(packet))
                else:
                    print(f"\n[INFO] Intermediate goal reached : '{goal_id}' (expected : '{SELECTED_GOAL}').")

async def handle_server_messages(ws):
    async for message in ws:
        data = json.loads(message)
        for cmd_packet in data:
            cmd = cmd_packet.get("cmd")

            if cmd == "RoomInfo":
                print("[AP] Connected to the room. Authenticating...")
                auth_packet = [{
                    "cmd": "Connect",
                    "password": "",
                    "game": GAME_NAME,
                    "name": SLOT_NAME,
                    "version": {"major": 0, "minor": 5, "build": 0, "class": "Version"},
                    "items_handling": 7,
                    "tags": ["AP"],
                    "uuid": "hoi4_client_unique_id"
                }]
                await ws.send(json.dumps(auth_packet))

            elif cmd == "Connected":
                print(f"[AP] Authentication successful ! Slot : {SLOT_NAME}")
                checked = cmd_packet.get("checked_locations", [])
                sent_locations.update(checked)

            elif cmd == "ReceivedItems":
                items = cmd_packet.get("items", [])
                commands_to_run = []

                for item in items:
                    item_id = item.get("item")
                    if item_id in ITEM_TABLE:
                        commands_to_run.append(ITEM_TABLE[item_id])
                    else:
                        print(f"[UNKNOWN ITEM] ID {item_id} not listed in ITEM_TABLE")

                if commands_to_run:
                    execute_hoi4_batch(commands_to_run)

async def main():
    while not GAME_LOG_PATH.exists():
        print(f"[*] Waiting for HOI4 (logs not found) : {GAME_LOG_PATH}")
        await asyncio.sleep(1)

    arm_console_hook()

    print(f"[*] Connecting to Archipelago server ({SERVER_URI})...")
    async with websockets.connect(SERVER_URI) as ws:
        await asyncio.gather(
            handle_server_messages(ws),
            watch_hoi4_logs(ws)
        )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopping the client.")