"""Inject ArchipelagoHOI4.dll into hoi4.exe.

Usage: python inject_dll.py [path\\to\\ArchipelagoHOI4.dll]
"""
import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

PIPE_NAME = "hoi4_ap"

PROCESS_CREATE_THREAD = 0x0002
PROCESS_VM_OPERATION = 0x0008
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_QUERY_INFORMATION = 0x0400
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
MEM_RELEASE = 0x8000
PAGE_READWRITE = 0x04
INFINITE = 0xFFFFFFFF

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
kernel32.VirtualFreeEx.restype = wintypes.BOOL
kernel32.VirtualFreeEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                        ctypes.POINTER(ctypes.c_size_t)]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetProcAddress.restype = ctypes.c_void_p
kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]
kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.CreateRemoteThread.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p,
                                        ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.GetExitCodeThread.restype = wintypes.BOOL
kernel32.GetExitCodeThread.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]


def fail(msg: str) -> bool:
    code = ctypes.get_last_error()
    print(f"[!] {msg} (erreur Windows {code} : {ctypes.FormatError(code).strip()})")
    return False


def get_hoi4_pid():
    out = subprocess.check_output(
        ["tasklist", "/FI", "IMAGENAME eq hoi4.exe", "/FO", "CSV", "/NH"],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ).decode("utf-8", errors="ignore")
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == "hoi4.exe":
            return int(parts[1])
    return None


def already_injected() -> bool:
    try:
        return PIPE_NAME in os.listdir(r"\\.\pipe\\")
    except OSError:
        return False


def inject(dll_path: Path) -> bool:
    dll_path = dll_path.resolve()
    if not dll_path.exists():
        print(f"[!] DLL introuvable : {dll_path}")
        return False

    pid = get_hoi4_pid()
    if not pid:
        print("[!] HOI4 n'est pas lancé.")
        return False

    if already_injected():
        print("[*] La DLL est déjà chargée dans HOI4 (pipe présent). Rien à faire.")
        return True

    print(f"[*] HOI4 détecté (PID {pid}), injection de {dll_path.name}...")
    access = (PROCESS_CREATE_THREAD | PROCESS_QUERY_INFORMATION | PROCESS_VM_OPERATION
              | PROCESS_VM_WRITE | PROCESS_VM_READ)
    h_process = kernel32.OpenProcess(access, False, pid)
    if not h_process:
        return fail("Impossible d'ouvrir HOI4")

    remote = None
    try:
        # LoadLibraryW with a UTF-16 path handles non-ASCII user folders
        path_buf = ctypes.create_unicode_buffer(str(dll_path))
        size = ctypes.sizeof(path_buf)
        remote = kernel32.VirtualAllocEx(h_process, None, size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not remote:
            return fail("VirtualAllocEx a échoué")

        written = ctypes.c_size_t(0)
        if not kernel32.WriteProcessMemory(h_process, remote, path_buf, size, ctypes.byref(written)) \
                or written.value != size:
            return fail("WriteProcessMemory a échoué")

        load_library = kernel32.GetProcAddress(kernel32.GetModuleHandleW("kernel32.dll"), b"LoadLibraryW")
        if not load_library:
            return fail("LoadLibraryW introuvable")

        h_thread = kernel32.CreateRemoteThread(h_process, None, 0, load_library, remote, 0, None)
        if not h_thread:
            return fail("CreateRemoteThread a échoué")
        try:
            kernel32.WaitForSingleObject(h_thread, INFINITE)
            exit_code = wintypes.DWORD(0)
            kernel32.GetExitCodeThread(h_thread, ctypes.byref(exit_code))
        finally:
            kernel32.CloseHandle(h_thread)

        # exit code = low 32 bits of the HMODULE returned by LoadLibraryW (0 = failure)
        if exit_code.value == 0:
            print("[!] LoadLibraryW a échoué dans HOI4 (DLL 32 bits ? dépendance manquante ? antivirus ?)")
            return False
    finally:
        if remote:
            kernel32.VirtualFreeEx(h_process, remote, 0, MEM_RELEASE)
        kernel32.CloseHandle(h_process)

    print("[+] DLL chargée. Détails dans Documents\\Paradox Interactive\\Hearts of Iron IV\\logs\\archipelago_dll.log")
    return True


if __name__ == "__main__":
    default = Path(__file__).parent / "ArchipelagoHOI4.dll"
    ok = inject(Path(sys.argv[1]) if len(sys.argv) > 1 else default)
    sys.exit(0 if ok else 1)
