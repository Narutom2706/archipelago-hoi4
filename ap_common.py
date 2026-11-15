"""Shared helpers: Windows/Steam paths and a minimal Paradox script parser."""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

GAME_FOLDER_NAME = "Hearts of Iron IV"
MOD_NAME = "Archipelago"


# --- Paths ---

def is_frozen() -> bool:
    """True when running from the PyInstaller executable."""
    return getattr(sys, "frozen", False)


def app_dir() -> Path:
    """Directory of the executable (or script). Holds the DLL and client state."""
    return Path(sys.executable).parent if is_frozen() else Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Path of a bundled resource (PyInstaller) or a file next to the script."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / name


def pe_timestamp(exe: Path) -> int | None:
    """PE TimeDateStamp of a Windows executable (changes with every game build)."""
    import struct
    try:
        with open(exe, "rb") as f:
            f.seek(0x3C)
            pe_offset = struct.unpack("<I", f.read(4))[0]
            f.seek(pe_offset + 8)
            return struct.unpack("<I", f.read(4))[0]
    except (OSError, struct.error):
        return None


def documents_dir() -> Path:
    """Documents folder, including OneDrive redirection."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        # FOLDERID_Documents = {FDD39AD0-238F-46AF-ADB4-6C85480369C7}
        folder_id = GUID(0xFDD39AD0, 0x238F, 0x46AF,
                         (ctypes.c_ubyte * 8)(0xAD, 0xB4, 0x6C, 0x85, 0x48, 0x03, 0x69, 0xC7))
        path_ptr = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(folder_id), 0, None, ctypes.byref(path_ptr))
        if hr == 0 and path_ptr.value:
            result = Path(path_ptr.value)
            ctypes.windll.ole32.CoTaskMemFree(path_ptr)
            return result
    return Path.home() / "Documents"


def hoi4_user_dir() -> Path:
    return documents_dir() / "Paradox Interactive" / GAME_FOLDER_NAME


def mod_dir() -> Path:
    return hoi4_user_dir() / "mod" / MOD_NAME


def _steam_roots() -> list[Path]:
    roots: list[Path] = []
    if sys.platform == "win32":
        import winreg
        for hive, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            try:
                with winreg.OpenKey(hive, key) as k:
                    roots.append(Path(winreg.QueryValueEx(k, value)[0]))
            except OSError:
                pass
    roots.append(Path(r"C:\Program Files (x86)\Steam"))

    libraries: list[Path] = []
    for root in roots:
        libraries.append(root)
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            text = vdf.read_text(encoding="utf-8", errors="ignore")
            for raw in re.findall(r'"path"\s+"([^"]+)"', text):
                libraries.append(Path(raw.replace("\\\\", "\\")))
    seen, unique = set(), []
    for lib in libraries:
        key = str(lib).lower()
        if key not in seen:
            seen.add(key)
            unique.append(lib)
    return unique


def find_hoi4_install() -> Path | None:
    for lib in _steam_roots():
        candidate = lib / "steamapps" / "common" / GAME_FOLDER_NAME
        if (candidate / "common").is_dir():
            return candidate
    return None


def ask_hoi4_install() -> Path:
    found = find_hoi4_install()
    if found:
        return found
    raw = input("Dossier d'installation de HOI4 introuvable. Chemin complet : ").strip().strip('"')
    path = Path(raw)
    if not (path / "common").is_dir():
        sys.exit(f"[!] {path} ne ressemble pas à une installation de HOI4.")
    return path


# --- Paradox script parser ---

def strip_comments(text: str) -> str:
    """Remove '#' comments, ignoring '#' inside quoted strings."""
    out = []
    for line in text.splitlines(keepends=True):
        in_str = False
        escaped = False
        for i, c in enumerate(line):
            if escaped:
                escaped = False
            elif c == "\\" and in_str:
                escaped = True
            elif c == '"':
                in_str = not in_str
            elif c == "#" and not in_str:
                newline = "\n" if line.endswith("\n") else ""
                line = line[:i].rstrip(" \t") + newline
                break
        out.append(line)
    return "".join(out)


TOKEN_RE = re.compile(r'"(?:[^"\\\n]|\\.)*"|[{}]|[<>!]?=|[<>]|[^\s{}=<>!"]+')


class ParseError(Exception):
    pass


@dataclass
class Node:
    """A 'key op value' statement, or a bare value inside a list."""
    key: str | None
    start: int                 # statement start (key, or value if bare)
    end: int                   # statement end (after '}' or the value)
    value: str | None = None   # scalar value
    value_start: int = -1
    value_end: int = -1
    open_pos: int = -1         # position of '{' for blocks
    close_pos: int = -1        # position of '}' for blocks
    children: list["Node"] = field(default_factory=list)

    @property
    def is_block(self) -> bool:
        return self.open_pos >= 0

    def find(self, key: str) -> list["Node"]:
        return [c for c in self.children if c.key == key]

    def first(self, key: str) -> "Node | None":
        found = self.find(key)
        return found[0] if found else None


def parse(text: str) -> Node:
    """Parse comment-free text (call strip_comments first)."""
    tokens = [(m.group(0), m.start(), m.end()) for m in TOKEN_RE.finditer(text)]
    pos = 0

    def parse_block(close_expected: bool) -> tuple[list[Node], int]:
        nonlocal pos
        items: list[Node] = []
        while pos < len(tokens):
            tok, s, e = tokens[pos]
            if tok == "}":
                if not close_expected:
                    raise ParseError(f"'}}' en trop à la position {s}")
                return items, s
            if tok == "{":
                # anonymous block inside a list
                pos += 1
                children, close = parse_block(True)
                items.append(Node(None, s, close + 1, open_pos=s, close_pos=close, children=children))
                pos += 1
                continue
            # key or bare value
            if pos + 1 < len(tokens) and tokens[pos + 1][0] in ("=", "<", ">", "<=", ">=", "!="):
                if pos + 2 >= len(tokens):
                    raise ParseError(f"valeur manquante après '{tok}' (position {s})")
                vtok, vs, ve = tokens[pos + 2]
                if vtok == "{":
                    pos += 3
                    children, close = parse_block(True)
                    items.append(Node(tok, s, close + 1, open_pos=vs, close_pos=close, children=children))
                    pos += 1
                elif vtok == "}":
                    raise ParseError(f"valeur manquante après '{tok}' (position {s})")
                else:
                    items.append(Node(tok, s, ve, value=vtok, value_start=vs, value_end=ve))
                    pos += 3
            else:
                items.append(Node(None, s, e, value=tok, value_start=s, value_end=e))
                pos += 1
        if close_expected:
            raise ParseError("fin de fichier atteinte avec un bloc non fermé")
        return items, len(text)

    children, _ = parse_block(False)
    return Node("<root>", 0, len(text), children=children)


def apply_edits(text: str, edits: list[tuple[int, int, str]]) -> str:
    """Apply (start, end, replacement) edits, last to first."""
    for start, end, repl in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        text = text[:start] + repl + text[end:]
    return text


def statement_span(text: str, node: Node) -> tuple[int, int]:
    """Extend a statement span to its leading indentation and trailing newline."""
    start, end = node.start, node.end
    while start > 0 and text[start - 1] in " \t":
        start -= 1
    if end < len(text) and text[end] == "\n":
        end += 1
    return start, end
