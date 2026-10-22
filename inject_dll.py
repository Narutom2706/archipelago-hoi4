import ctypes
from ctypes import wintypes
import os
import subprocess
import sys

PROCESS_ALL_ACCESS = 0x1F0FFF
MEM_COMMIT = 0x1000
MEM_RESERVE = 0x2000
PAGE_READWRITE = 0x04

kernel32 = ctypes.windll.kernel32
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

kernel32.VirtualAllocEx.restype = ctypes.c_void_p
kernel32.VirtualAllocEx.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    wintypes.DWORD,
    wintypes.DWORD,
]

kernel32.WriteProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
]

kernel32.GetModuleHandleA.restype = wintypes.HMODULE
kernel32.GetModuleHandleA.argtypes = [ctypes.c_char_p]

kernel32.GetProcAddress.restype = ctypes.c_void_p
kernel32.GetProcAddress.argtypes = [wintypes.HMODULE, ctypes.c_char_p]

kernel32.CreateRemoteThread.restype = wintypes.HANDLE
kernel32.CreateRemoteThread.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_size_t,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
]

kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]


def get_hoi4_pid():
    raw_output = subprocess.check_output(
        'tasklist /FI "IMAGENAME eq hoi4.exe" /NH', shell=True
    )
    output = raw_output.decode("utf-8", errors="ignore")
    for line in output.strip().splitlines():
        parts = line.split()
        if len(parts) >= 2 and "hoi4.exe" in parts[0].lower():
            return int(parts[1])
    return None


def inject(dll_path: str):
    dll_path = os.path.abspath(dll_path)
    if not os.path.exists(dll_path):
        print(f"[!] File DLL not found : {dll_path}")
        return False

    pid = get_hoi4_pid()
    if not pid:
        print("[!] HOI4 is not running !")
        return False

    print(f"[*] HOI4 detected (PID : {pid}). Injecting 64-bit DLL...")
    h_process = kernel32.OpenProcess(PROCESS_ALL_ACCESS, False, pid)
    if not h_process:
        print(
            "[!] Unable to open HOI4 (run the terminal as Administrator)."
        )
        return False

    dll_bytes = dll_path.encode("utf-8") + b"\0"
    arg_address = kernel32.VirtualAllocEx(
        h_process, None, len(dll_bytes), MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE
    )

    bytes_written = ctypes.c_size_t(0)
    kernel32.WriteProcessMemory(
        h_process,
        arg_address,
        dll_bytes,
        len(dll_bytes),
        ctypes.byref(bytes_written),
    )

    h_kernel32 = kernel32.GetModuleHandleA(b"kernel32.dll")
    load_library_addr = kernel32.GetProcAddress(h_kernel32, b"LoadLibraryA")

    h_thread = kernel32.CreateRemoteThread(
        h_process, None, 0, load_library_addr, arg_address, 0, None
    )
    kernel32.WaitForSingleObject(h_thread, 0xFFFFFFFF)

    kernel32.CloseHandle(h_thread)
    kernel32.CloseHandle(h_process)

    print("[+] DLL injected with success without memory corruption !")
    return True


if __name__ == "__main__":
    dll_file = sys.argv[1] if len(sys.argv) > 1 else "ArchipelagoHOI4.dll"
    inject(dll_file)