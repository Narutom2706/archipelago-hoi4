"""Generate a byte signature for the hoi4.exe console command function.

Relative addresses (call, jmp, rip-relative) become wildcards.

    pip install pefile capstone
    python tools/make_signature.py [path\\to\\hoi4.exe] [rva]

Paste the printed line into cfg::SIGNATURE (dllmain.cpp).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import pefile
    from capstone import CS_ARCH_X86, CS_MODE_64, Cs
    from capstone.x86 import X86_OP_IMM, X86_OP_MEM, X86_REG_RIP
except ImportError:
    sys.exit("pip install pefile capstone")

DEFAULT_RVA = 0x24B4D60
MAX_LEN = 160
# long enough to go past the function prologue
MIN_LEN = 48


def wildcard_mask(code: bytes, address: int) -> tuple[list[bool], list[int]]:
    """Return (mask, instruction ends). False marks a wildcard (relative address)."""
    md = Cs(CS_ARCH_X86, CS_MODE_64)
    md.detail = True
    keep = [True] * len(code)
    offset = 0
    boundaries = []
    for insn in md.disasm(code, address):
        start = insn.address - address
        disp_off, disp_size = insn.disp_offset, insn.disp_size
        imm_off, imm_size = insn.imm_offset, insn.imm_size
        relative_branch = insn.group(1) or insn.group(2) or insn.group(7)  # jump, call, branch_relative
        for op in insn.operands:
            if op.type == X86_OP_MEM and op.mem.base == X86_REG_RIP and disp_size:
                for i in range(disp_off, disp_off + disp_size):
                    keep[start + i] = False
            if op.type == X86_OP_IMM and relative_branch and imm_size:
                for i in range(imm_off, imm_off + imm_size):
                    keep[start + i] = False
        offset = start + insn.size
        boundaries.append(offset)
    return keep[:offset], boundaries


def count_matches(data: bytes, pattern: list[int | None], limit: int = 2) -> int:
    first_fixed = next(i for i, b in enumerate(pattern) if b is not None)
    needle = pattern[first_fixed]
    count, pos = 0, data.find(bytes([needle]))
    while pos != -1:
        start = pos - first_fixed
        if start >= 0 and start + len(pattern) <= len(data):
            if all(b is None or data[start + i] == b for i, b in enumerate(pattern)):
                count += 1
                if count >= limit:
                    return count
        pos = data.find(bytes([needle]), pos + 1)
    return count


def make_signature(exe: Path, rva: int) -> str:
    pe = pefile.PE(str(exe), fast_load=True)
    text = next(s for s in pe.sections if s.Characteristics & 0x20000000)
    data = text.get_data()
    off = rva - text.VirtualAddress
    if not 0 <= off < len(data):
        sys.exit(f"[!] RVA 0x{rva:X} hors de la section de code")

    code = data[off:off + MAX_LEN]
    keep, boundaries = wildcard_mask(code, pe.OPTIONAL_HEADER.ImageBase + rva)
    pattern = [b if k else None for b, k in zip(code, keep)]

    # cut on instruction boundaries only
    for length in boundaries:
        if length < MIN_LEN:
            continue
        candidate = pattern[:length]
        if count_matches(data, candidate) == 1:
            jokers = candidate.count(None)
            print(f"[*] {length} octets, {jokers} jokers, {sum(1 for b in boundaries if b <= length)} instructions")
            return " ".join("?" if b is None else f"{b:02X}" for b in candidate)
    sys.exit("[!] Impossible de trouver une signature unique")


def main() -> None:
    if len(sys.argv) > 1:
        exe = Path(sys.argv[1].strip('"'))
    else:
        from ap_common import ask_hoi4_install
        exe = ask_hoi4_install() / "hoi4.exe"
    rva = int(sys.argv[2], 16) if len(sys.argv) > 2 else DEFAULT_RVA
    pe = pefile.PE(str(exe), fast_load=True)
    print(f"[*] {exe} TimeDateStamp={pe.FILE_HEADER.TimeDateStamp}")
    sig = make_signature(exe, rva)
    print(f'\nconstexpr const char* SIGNATURE = "{sig}";')


if __name__ == "__main__":
    main()
