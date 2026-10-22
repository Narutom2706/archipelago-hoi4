import os
import re
import time
from pathlib import Path
from pynput.keyboard import Controller, Key

DOCS_HOI4 = Path(os.path.expanduser("~")) / "Documents" / "Paradox Interactive" / "Hearts of Iron IV"
GAME_LOG_PATH = DOCS_HOI4 / "logs" / "game.log"
CMD_FILE = DOCS_HOI4 / "ap_cmd.txt"

RE_CHECK = re.compile(r"AP_CHECK:([A-Za-z0-9_]+)")
keyboard = Controller()

def execute_hoi4_command(command: str):
    with open(CMD_FILE, "w", encoding="utf-8") as f:
        f.write(command + "\n")

    time.sleep(2)
    keyboard.press('²')
    keyboard.release('²')
    time.sleep(0.15)

    keyboard.press(Key.backspace)
    keyboard.release(Key.backspace)
    time.sleep(0.05)

    keyboard.type("run ap_cmd.txt")
    time.sleep(0.05)
    keyboard.press(Key.enter)
    keyboard.release(Key.enter)
    time.sleep(0.05)

    keyboard.press('²')
    keyboard.release('²')

def watch_bridge():
    print(f"[*] Waiting for file : {GAME_LOG_PATH}")
    while not GAME_LOG_PATH.exists():
        time.sleep(1)

    print("[+] Bridge connected to game.log")
    with open(GAME_LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
        f.seek(0, os.SEEK_END)

        while True:
            line = f.readline()
            if not line:
                f.seek(f.tell()) 
                time.sleep(0.1)
                continue

            match = RE_CHECK.search(line)
            if match:
                check_id = match.group(1)
                print(f"\n[CHECK DETECTED] -> {check_id}")

                print("[->] Injecting the key for the Four-Year Plan...")
                execute_hoi4_command("set_country_flag ap_key_GER_the_four_year_plan GER")

if __name__ == "__main__":
    watch_bridge()