r"""Archipelago client for Hearts of Iron IV.

  HOI4 --(game.log: AP_CHECK / AP_GOAL)--> client --(websocket)--> AP server
  AP server --(ReceivedItems)--> client --(pipe \\.\pipe\hoi4_ap)--> DLL --> game console
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import re
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import websockets
from pynput.keyboard import Controller, Key

from ap_common import app_dir, hoi4_user_dir, resource_path

# --- Configuration ---
GAME_NAME = "Hearts of Iron IV"
CLIENT_VERSION = {"major": 0, "minor": 5, "build": 0, "class": "Version"}
DEFAULT_GOAL = "defeat_allies"          # overridden by slot_data["goal"]
PLAYER_TAG = "GER"
ARM_COMMAND = "init"                    # typed in the console to arm the hook
CONSOLE_SCANCODE = 0x29                 # key below Esc, any keyboard layout

MAPPING_FILE = resource_path("mapping_germany.json")   # bundled in the executable
STATE_DIR = app_dir() / "ap_state"
DLL_PATH = app_dir() / "ArchipelagoHOI4.dll"
BASE_ITEM_ID = 941000
NOTIFY_SUMMARY_ABOVE = 3   # more keys at once -> single summary event
GAME_LOG_PATH = hoi4_user_dir() / "logs" / "game.log"
PIPE_PATH = r"\\.\pipe\hoi4_ap"

# One-shot items (applied once, unlike idempotent key flags)
EXTRA_ITEMS = {
    949001: "pp 150",
    949002: "add_equipment 8000 infantry_equipment_1",
    949003: "add_equipment 1000 artillery_equipment_1",
}

def hoi4_pid() -> int | None:
    from inject_dll import get_hoi4_pid
    try:
        return get_hoi4_pid()
    except Exception:
        return None


def pipe_exists() -> bool:
    from inject_dll import already_injected
    try:
        return already_injected()
    except Exception:
        return False


def inject_dll(path: Path) -> bool:
    from inject_dll import inject
    return inject(path)


class ServerRefused(Exception):
    pass


RE_CHECK = re.compile(r"AP_CHECK:([A-Za-z0-9_]+)")
RE_GOAL = re.compile(r"AP_GOAL:([A-Za-z0-9_]+)")


def load_mapping() -> tuple[dict[str, int], dict[int, str]]:
    if not MAPPING_FILE.exists():
        print("[!] mapping_germany.json introuvable : lance d'abord patch_germany_tree.py")
        return {}, dict(EXTRA_ITEMS)
    data = json.loads(MAPPING_FILE.read_text(encoding="utf-8"))
    locations = {k: int(v) for k, v in data.get("locations", {}).items()}
    items = {int(k): v for k, v in data.get("items", {}).items()}
    items.update(EXTRA_ITEMS)
    print(f"[+] {len(locations)} checks et {len(items)} items chargés.")
    return locations, items


def is_idempotent(command: str) -> bool:
    return command.startswith("set_country_flag ")


# --- DLL pipe (blocking, use via asyncio.to_thread) ---
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
OPEN_EXISTING = 3
PIPE_READMODE_MESSAGE = 0x2
ERROR_PIPE_BUSY = 231
ERROR_MORE_DATA = 234
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

if sys.platform == "win32":
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    k32.SetNamedPipeHandleState.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD),
                                            ctypes.c_void_p, ctypes.c_void_p]
    k32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                              ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]


class DllPipe:
    """Persistent connection to the DLL pipe. request() returns None if unreachable."""

    def __init__(self) -> None:
        self.handle = None
        self.lock = threading.Lock()

    def _open(self) -> bool:
        for _ in range(2):
            h = k32.CreateFileW(PIPE_PATH, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, None)
            if h and h != INVALID_HANDLE_VALUE:
                mode = wintypes.DWORD(PIPE_READMODE_MESSAGE)
                k32.SetNamedPipeHandleState(h, ctypes.byref(mode), None, None)
                self.handle = h
                return True
            if ctypes.get_last_error() != ERROR_PIPE_BUSY:
                return False
            k32.WaitNamedPipeW(PIPE_PATH, 2000)
        return False

    def close(self) -> None:
        if self.handle:
            k32.CloseHandle(self.handle)
            self.handle = None

    def request(self, message: str) -> str | None:
        with self.lock:
            if not self.handle and not self._open():
                return None
            data = message.encode("utf-8")
            written = wintypes.DWORD(0)
            if not k32.WriteFile(self.handle, data, len(data), ctypes.byref(written), None):
                self.close()
                return None
            buf = ctypes.create_string_buffer(4096)
            read = wintypes.DWORD(0)
            if not k32.ReadFile(self.handle, buf, len(buf), ctypes.byref(read), None) \
                    and ctypes.get_last_error() != ERROR_MORE_DATA:
                self.close()
                return None
            return buf.raw[:read.value].decode("utf-8", errors="replace")


# --- Keyboard input ---
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def press_scancode(scancode: int) -> None:
    """Press a physical key regardless of keyboard layout."""
    KEYEVENTF_SCANCODE, KEYEVENTF_KEYUP = 0x0008, 0x0002
    for flags in (KEYEVENTF_SCANCODE, KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP):
        inp = INPUT(type=1)
        inp.ki = KEYBDINPUT(0, scancode, flags, 0, 0)
        ctypes.windll.user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.03)


def find_hoi4_window() -> int:
    """Main window of hoi4.exe, matched by process."""
    user32, k32_ = ctypes.windll.user32, ctypes.windll.kernel32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = k32_.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if h:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if k32_.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) and \
                    buf.value.lower().endswith("\\hoi4.exe"):
                found.append(hwnd)
            k32_.CloseHandle(h)
        return not found

    user32.EnumWindows(callback, 0)
    return found[0] if found else user32.FindWindowW(None, "Hearts of Iron IV")


def focus_hoi4() -> bool:
    """Bring HOI4 to the foreground (Alt press + AttachThreadInput), waiting up to 3 s."""
    user32, k32_ = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = find_hoi4_window()
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    fg_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
    my_thread = k32_.GetCurrentThreadId()
    attached = fg_thread and fg_thread != my_thread and user32.AttachThreadInput(my_thread, fg_thread, True)
    try:
        user32.keybd_event(0x12, 0, 0, 0)  # Alt down unlocks SetForegroundWindow
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
        user32.keybd_event(0x12, 0, 2, 0)  # Alt up
    finally:
        if attached:
            user32.AttachThreadInput(my_thread, fg_thread, False)
    for _ in range(30):
        if user32.GetForegroundWindow() == hwnd:
            time.sleep(0.5)  # let the game switch back to fullscreen
            return True
        time.sleep(0.1)
    return False


def type_in_console(command: str) -> bool:
    if not focus_hoi4():
        print("[!] Windows refuse de passer HOI4 au premier plan : clique sur la fenêtre du jeu, "
              "je réessaie dans 20 s (ou tape n'importe quelle commande dans la console).")
        return False
    kb = Controller()
    press_scancode(CONSOLE_SCANCODE)
    time.sleep(0.2)
    kb.press(Key.backspace)
    kb.release(Key.backspace)
    kb.type(command)
    time.sleep(0.05)
    kb.press(Key.enter)
    kb.release(Key.enter)
    time.sleep(0.15)
    press_scancode(CONSOLE_SCANCODE)
    return True


# --- Persistent state (per seed + slot) ---
class State:
    def __init__(self, seed: str, slot: str) -> None:
        STATE_DIR.mkdir(exist_ok=True)
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", f"{seed}_{slot}")
        self.path = STATE_DIR / f"{safe}.json"
        data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.applied_index: int = data.get("applied_index", 0)

    def save(self) -> None:
        self.path.write_text(json.dumps({"applied_index": self.applied_index}), encoding="utf-8")


LAST_CONNECTION = STATE_DIR / "last_connection.json"


def load_last_connection() -> dict:
    try:
        return json.loads(LAST_CONNECTION.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_last_connection(server: str, slot: str) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    LAST_CONNECTION.write_text(json.dumps({"server": server, "slot": slot}), encoding="utf-8")


def client_uuid() -> str:
    STATE_DIR.mkdir(exist_ok=True)
    path = STATE_DIR / "uuid.txt"
    if not path.exists():
        path.write_text(uuid.uuid4().hex, encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


# --- Client ---
class Hoi4Client:
    def __init__(self, server_uri: str, slot: str, password: str) -> None:
        self.server_uri = server_uri
        self.slot = slot
        self.password = password
        self.locations, self.items = load_mapping()
        self.pipe = DllPipe()

        self.ws = None
        self.state: State | None = None
        self.goal = DEFAULT_GOAL
        self.goal_sent = False
        self.seen_goals: set[str] = set()       # goals seen in game.log
        self.checked: set[int] = set()        # known to the server
        self.pending_checks: set[int] = set()  # seen in game.log, not sent yet
        self.received: list[int] = []
        self.item_queue: asyncio.Queue[tuple[int, int, str]] = asyncio.Queue()
        self.dll_ready = asyncio.Event()
        self.in_game = False                    # a campaign is running
        self.new_keys: list[int] = []           # newly received keys to notify
        self.inject_tried_pid: int | None = None

    # --- server ---
    async def send(self, packets: list[dict]) -> None:
        if self.ws is not None:
            await self.ws.send(json.dumps(packets))

    async def flush_checks(self) -> None:
        new = sorted(self.pending_checks - self.checked)
        self.pending_checks.clear()
        if new and self.ws is not None:
            await self.send([{"cmd": "LocationChecks", "locations": new}])
            self.checked.update(new)
            print(f"[CHECK] {len(new)} check(s) envoyé(s)")
        elif new:
            self.pending_checks.update(new)  # retry on next connection

    async def handle_packet(self, packet: dict) -> None:
        cmd = packet.get("cmd")
        if cmd == "RoomInfo":
            self.state = State(packet.get("seed_name", "unknown"), self.slot)
            await self.send([{
                "cmd": "Connect", "password": self.password, "game": GAME_NAME, "name": self.slot,
                "version": CLIENT_VERSION, "items_handling": 0b111, "tags": ["AP"],
                "uuid": client_uuid(), "slot_data": True,
            }])
        elif cmd == "ConnectionRefused":
            errors = packet.get("errors", []) or ["raison inconnue"]
            hints = {
                "InvalidSlot": f"aucun joueur « {self.slot} » dans cette partie : utilise exactement "
                               f"le name: de ton YAML (il apparaît aussi dans le spoiler).",
                "InvalidGame": "ce slot n'est pas une partie Hearts of Iron IV.",
                "InvalidPassword": "mot de passe incorrect.",
                "IncompatibleVersion": "version du client trop ancienne pour ce serveur.",
            }
            details = "\n".join(f"    -> {hints[e]}" for e in errors if e in hints)
            raise ServerRefused(f"Connexion refusée par le serveur : {', '.join(errors)}"
                                + (f"\n{details}" if details else ""))
        elif cmd == "Connected":
            self.checked = set(packet.get("checked_locations", []))
            self.goal = (packet.get("slot_data") or {}).get("goal", DEFAULT_GOAL)
            print(f"[AP] Connecté ! Slot : {self.slot} | objectif : {self.goal}")
            save_last_connection(self.server_uri, self.slot)
            await self.flush_checks()
            await self.maybe_send_goal()
        elif cmd == "ReceivedItems":
            await self.on_received_items(packet.get("index", 0), packet.get("items", []))
        elif cmd == "PrintJSON":
            text = "".join(part.get("text", "") for part in packet.get("data", []))
            if text:
                print(f"[AP] {text}")

    async def on_received_items(self, index: int, items: list[dict]) -> None:
        ids = [it.get("item") for it in items]
        if index == 0:
            # full list (connect or resync)
            self.received = ids
            while not self.item_queue.empty():
                self.item_queue.get_nowait()
            start = 0
        elif index == len(self.received):
            self.received.extend(ids)
            start = index
        else:
            print(f"[AP] Désynchronisation des items ({index} != {len(self.received)}), resync...")
            await self.send([{"cmd": "Sync"}])
            return

        applied = self.state.applied_index if self.state else 0
        for pos in range(start, len(self.received)):
            item_id = self.received[pos]
            command = self.items.get(item_id)
            if command is None:
                print(f"[!] Item inconnu {item_id} (absent du mapping)")
                continue
            # key flags are re-applied on every connection, one-shot items only once
            if pos >= applied or is_idempotent(command):
                await self.item_queue.put((pos, item_id, command))

    # --- DLL ---
    async def dll_request(self, message: str) -> str | None:
        return await asyncio.to_thread(self.pipe.request, message)

    async def watch_dll(self) -> None:
        """Track DLL state, inject it and arm the console hook when needed."""
        last_reason = None
        arm_attempts = 0
        last_arm = 0.0
        while True:
            reply = await self.dll_request("PING")
            if reply == "READY":
                if not self.dll_ready.is_set():
                    print("[+] DLL prête : les items vont être appliqués.")
                    self.dll_ready.set()
                arm_attempts = 0
            else:
                self.dll_ready.clear()
                reason = "dll_absente" if reply is None else reply.split(":", 1)[-1]
                if reason == "dll_absente" and await asyncio.to_thread(pipe_exists):
                    reason = "pipe_occupe"   # DLL is running, another client holds the pipe
                if reason == "dll_absente":
                    pid = await asyncio.to_thread(hoi4_pid)
                    reason = "hoi4_absent" if pid is None else reason
                    if pid is not None and pid != self.inject_tried_pid:
                        self.inject_tried_pid = pid
                        print("[*] HOI4 détecté : injection de la DLL...")
                        await asyncio.to_thread(inject_dll, DLL_PATH)
                        await asyncio.sleep(2)   # wait for the DLL to open its pipe
                        continue
                if reason != last_reason:
                    print(f"[DLL] Pas prête : {reason}")
                    if reason == "hoi4_absent":
                        print("      -> lance HOI4, je m'occupe du reste")
                    elif reason == "dll_absente":
                        print("      -> l'injection automatique a échoué (voir le message ci-dessus)")
                    elif reason == "pipe_occupe":
                        print("      -> la DLL est déjà utilisée par un autre client : ferme l'autre fenêtre "
                              "du client HOI4 Archipelago")
                    elif reason == "unsupported_version":
                        print("      -> version de HOI4 non supportée par la DLL (voir archipelago_dll.log)")
                    elif reason == "console_not_captured" and not self.in_game:
                        print("      -> j'armerai la console dès que ta partie sera lancée")
                    last_reason = reason
                if reason == "console_not_captured" and self.in_game and time.monotonic() - last_arm > 20:
                    if arm_attempts < 3:
                        arm_attempts += 1
                        print(f"[*] Armement du hook (essai {arm_attempts}/3)...")
                        await asyncio.to_thread(type_in_console, ARM_COMMAND)
                        last_arm = time.monotonic()
                    elif arm_attempts == 3:
                        arm_attempts += 1
                        print("[!] Armement automatique raté : ouvre la console et tape n'importe quelle commande.")
            if reply == "READY":
                last_reason = None
            await asyncio.sleep(1)

    async def apply_items(self) -> None:
        while True:
            pos, item_id, command = await self.item_queue.get()
            if self.state and pos < self.state.applied_index and not is_idempotent(command):
                continue  # one-shot item already applied
            while True:
                await self.dll_ready.wait()
                reply = await self.dll_request(f"CMD {command}")
                if reply == "OK":
                    print(f"[ITEM] {command}")
                    if self.state and pos >= self.state.applied_index:
                        if is_idempotent(command) and item_id - BASE_ITEM_ID > 0:
                            self.new_keys.append(item_id - BASE_ITEM_ID)   # new key, not a replay
                        self.state.applied_index = pos + 1
                        self.state.save()
                    break
                if reply in (None, "TIMEOUT") or (reply or "").startswith("NOT_READY"):
                    await asyncio.sleep(1)   # not executed by the game, retry
                    continue
                print(f"[!] Échec de '{command}' : {reply}. Les items restants sont en pause.")
                self.dll_ready.clear()
                await asyncio.sleep(5)

    async def notify_keys(self) -> None:
        """Fire in-game events for received keys, batching bursts."""
        while True:
            await asyncio.sleep(1.5)
            if not self.new_keys or not self.dll_ready.is_set() or not self.item_queue.empty():
                continue
            keys, self.new_keys = self.new_keys, []
            events = ["ap_key.0"] if len(keys) > NOTIFY_SUMMARY_ABOVE else [f"ap_key.{n}" for n in keys]
            for event in events:
                await self.dll_request(f"CMD event {event} {PLAYER_TAG}")

    # --- game.log ---
    async def watch_game_log(self) -> None:
        pos, first_line = 0, b""
        print(f"[*] Surveillance de {GAME_LOG_PATH}")
        while True:
            await asyncio.sleep(0.5)
            try:
                with open(GAME_LOG_PATH, "rb") as f:
                    head = f.readline()
                    f.seek(0, 2)
                    size = f.tell()
                    if size < pos or head != first_line:   # new game session
                        pos, first_line = 0, head
                        self.in_game = False
                    f.seek(pos)
                    chunk = f.read()
            except FileNotFoundError:
                continue
            # only complete lines
            end = chunk.rfind(b"\n") + 1
            if end == 0:
                continue
            pos += end
            for raw in chunk[:end].decode("utf-8", errors="ignore").splitlines():
                await self.on_log_line(raw)
            await self.flush_checks()

    async def on_log_line(self, line: str) -> None:
        if not self.in_game and ("Launching SINGLEPLAYER-game" in line or "Start RestoreDeviceObjects" in line):
            self.in_game = True
        m = RE_CHECK.search(line)
        if m:
            loc = self.locations.get(m.group(1))
            if loc is None:
                print(f"[!] Check inconnu : {m.group(1)}")
            elif loc not in self.checked:
                self.pending_checks.add(loc)
        m = RE_GOAL.search(line)
        if m:
            self.seen_goals.add(m.group(1))
            await self.maybe_send_goal()

    async def maybe_send_goal(self) -> None:
        if self.goal in self.seen_goals and not self.goal_sent and self.ws is not None:
            print(f"[VICTOIRE] Objectif atteint ({self.goal}) !")
            await self.send([{"cmd": "StatusUpdate", "status": 30}])
            self.goal_sent = True

    # --- main loop ---
    async def server_loop(self) -> None:
        delay, last_error = 5, None
        while True:
            connected = False
            try:
                if last_error is None:
                    print(f"[*] Connexion à {self.server_uri}...")
                async with websockets.connect(self.server_uri, max_size=None) as ws:
                    self.ws, connected, delay, last_error = ws, True, 5, None
                    async for message in ws:
                        for packet in json.loads(message):
                            await self.handle_packet(packet)
            except (OSError, websockets.exceptions.WebSocketException) as e:
                if connected:
                    error = f"[!] Connexion au serveur perdue ({e})."
                elif isinstance(e, ConnectionRefusedError) or getattr(e, "winerror", None) == 1225:
                    error = (f"[!] Serveur injoignable sur {self.server_uri} : il n'est pas lancé, "
                             "ou l'adresse/le port est faux.")
                else:
                    error = f"[!] Impossible de joindre le serveur ({e})."
                if error != last_error:   # print each distinct error once
                    print(error + " Je réessaie en boucle...")
                    last_error = error
            finally:
                self.ws = None
                self.goal_sent = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)

    async def run(self) -> None:
        await asyncio.gather(self.server_loop(), self.watch_game_log(),
                             self.watch_dll(), self.apply_items(), self.notify_keys())


def ask_server(default: str) -> str:
    raw = input(f"Adresse du serveur AP [{default}] : ").strip() or default
    if raw.startswith(("ws://", "wss://")):
        return raw
    return f"wss://{raw}" if "archipelago.gg" in raw else f"ws://{raw}"


def main() -> None:
    last = load_last_connection()
    server = ask_server(last.get("server", "localhost:38281").replace("ws://", ""))
    default_slot = last.get("slot", "")
    slot = input(f"Nom du joueur / slot [{default_slot}] : ").strip() or default_slot
    if not slot:
        sys.exit("[!] Il faut un nom de slot (le name: de ton YAML).")
    password = input("Mot de passe (vide si aucun) : ").strip()
    try:
        asyncio.run(Hoi4Client(server, slot, password).run())
    except ServerRefused as e:
        print(f"[!] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nArrêt du client.")


if __name__ == "__main__":
    main()
