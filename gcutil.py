#!/usr/bin/python
# -*- coding: utf-8 -*-
#
# 参考 https://github.com/apangin/hsperf/blob/main/hsperf.c
#

import ctypes
import os
import platform
import struct
import sys


ELF_MAGIC = b"\x7fELF"
ELF_CLASS64 = 2
ELF_DATA2LSB = 1
ELF_VERSION_CURRENT = 1
ELF_HEADER_FORMAT = "<16sHHIQQQIHHHHHH"
SECTION_HEADER_FORMAT = "<IIQQQQIIQQ"
SYMBOL_FORMAT = "<IBBHQQ"
SHT_SYMTAB = 2
SHT_DYNSYM = 11

GC_COUNTER_NAMES = {
    "s0u": "sun.gc.generation.0.space.1.used",
    "s0c": "sun.gc.generation.0.space.1.capacity",
    "s1u": "sun.gc.generation.0.space.2.used",
    "s1c": "sun.gc.generation.0.space.2.capacity",
    "eu": "sun.gc.generation.0.space.0.used",
    "ec": "sun.gc.generation.0.space.0.capacity",
    "ou": "sun.gc.generation.1.space.0.used",
    "oc": "sun.gc.generation.1.space.0.capacity",
    "mu": "sun.gc.metaspace.used",
    "mc": "sun.gc.metaspace.capacity",
    "ccsu": "sun.gc.compressedclassspace.used",
    "ccsc": "sun.gc.compressedclassspace.capacity",
    "ygc": "sun.gc.collector.0.invocations",
    "ygct": "sun.gc.collector.0.time",
    "fgc": "sun.gc.collector.1.invocations",
    "fgct": "sun.gc.collector.1.time",
    "cgc": "sun.gc.collector.2.invocations",
    "cgct": "sun.gc.collector.2.time",
}


class IOVec(ctypes.Structure):
    _fields_ = [("iov_base", ctypes.c_void_p), ("iov_len", ctypes.c_size_t)]


LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.syscall.argtypes = [ctypes.c_long]

PROCESS_VM_READV_NUMBERS = {
    "x86_64": 310,
    "aarch64": 270,
    "arm64": 270,
}


def locate_libjvm(pid: int):
    maps_path = f"/proc/{pid}/maps"
    with open(maps_path, "r", encoding="utf-8") as maps_file:
        for line in maps_file:
            if not line.rstrip("\n").endswith("/libjvm.so"):
                continue
            parts = line.split()
            address_range = parts[0]
            offset_hex = parts[2]
            path = parts[-1]
            start_hex = address_range.split("-", 1)[0]
            base_addr = int(start_hex, 16) - int(offset_hex, 16)
            return path, base_addr
    raise RuntimeError("Could not locate loaded libjvm.so")


def parse_elf_symbols(file_name: str, base_addr: int):
    with open(file_name, "rb") as elf_file:
        data = elf_file.read()

    ident = data[:16]
    if ident[:4] != ELF_MAGIC or ident[4] != ELF_CLASS64 or ident[5] != ELF_DATA2LSB or ident[6] != ELF_VERSION_CURRENT:
        raise RuntimeError("Failed to parse libjvm.so")

    header = struct.unpack_from(ELF_HEADER_FORMAT, data, 0)
    e_shoff = header[6]
    e_shentsize = header[11]
    e_shnum = header[12]

    sections = []
    for index in range(e_shnum):
        offset = e_shoff + index * e_shentsize
        sections.append(struct.unpack_from(SECTION_HEADER_FORMAT, data, offset))

    symtab = None
    for section in sections:
        if section[1] in (SHT_SYMTAB, SHT_DYNSYM):
            symtab = section
            break
    if symtab is None:
        raise RuntimeError("Failed to parse libjvm.so")

    strtab = sections[symtab[6]]
    strings = data[strtab[4]:strtab[4] + strtab[5]]
    symbol_offset = symtab[4]
    symbol_size = symtab[5]
    symbol_entry_size = symtab[9]

    result = {
        "perf_start": None,
        "perf_top": None,
        "entry": None,
        "stride": None,
        "type_offset": None,
        "field_offset": None,
        "address_offset": None,
    }

    for offset in range(symbol_offset, symbol_offset + symbol_size, symbol_entry_size):
        st_name, _, _, _, st_value, _ = struct.unpack_from(SYMBOL_FORMAT, data, offset)
        if st_name == 0 or st_value == 0:
            continue
        end = strings.find(b"\0", st_name)
        if end == -1:
            continue
        name = strings[st_name:end].decode("utf-8", errors="ignore")
        if name == "_ZN10PerfMemory6_startE":
            result["perf_start"] = base_addr + st_value
        elif name == "_ZN10PerfMemory4_topE":
            result["perf_top"] = base_addr + st_value
        elif name == "gHotSpotVMStructs":
            result["entry"] = base_addr + st_value
        elif name == "gHotSpotVMStructEntryArrayStride":
            result["stride"] = base_addr + st_value
        elif name == "gHotSpotVMStructEntryTypeNameOffset":
            result["type_offset"] = base_addr + st_value
        elif name == "gHotSpotVMStructEntryFieldNameOffset":
            result["field_offset"] = base_addr + st_value
        elif name == "gHotSpotVMStructEntryAddressOffset":
            result["address_offset"] = base_addr + st_value

    return result


def process_vm_readv_number() -> int:
    machine = platform.machine().lower()
    if machine in PROCESS_VM_READV_NUMBERS:
        return PROCESS_VM_READV_NUMBERS[machine]
    raise RuntimeError(f"process_vm_readv syscall number is unknown for architecture: {machine}")


def process_vm_read(pid: int, remote_addr: int, size: int) -> bytes:
    buffer = ctypes.create_string_buffer(size)
    local_iov = IOVec(ctypes.cast(buffer, ctypes.c_void_p), size)
    remote_iov = IOVec(ctypes.c_void_p(remote_addr), size)

    if hasattr(LIBC, "process_vm_readv"):
        process_vm_readv_func = LIBC.process_vm_readv
        process_vm_readv_func.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(IOVec),
            ctypes.c_ulong,
            ctypes.POINTER(IOVec),
            ctypes.c_ulong,
            ctypes.c_ulong,
        ]
        process_vm_readv_func.restype = ctypes.c_ssize_t
        nread = process_vm_readv_func(
            pid,
            ctypes.byref(local_iov),
            1,
            ctypes.byref(remote_iov),
            1,
            0,
        )
    else:
        nread = LIBC.syscall(
            process_vm_readv_number(),
            ctypes.c_int(pid),
            ctypes.byref(local_iov),
            ctypes.c_ulong(1),
            ctypes.byref(remote_iov),
            ctypes.c_ulong(1),
            ctypes.c_ulong(0),
        )

    if nread < 0:
        errno = ctypes.get_errno()
        raise OSError(errno, os.strerror(errno))
    if nread != size:
        raise OSError(f"short read: expected {size}, got {nread}")
    return buffer.raw


def read_pointer(pid: int, remote_addr: int) -> int:
    return struct.unpack("<Q", process_vm_read(pid, remote_addr, 8))[0]


def read_c_string(pid: int, remote_addr: int, max_size: int = 64) -> bytes:
    data = process_vm_read(pid, remote_addr, max_size)
    end = data.find(b"\0")
    return data if end == -1 else data[:end]


def read_vmstructs(pid: int, symbols):
    if symbols["perf_start"] is not None and symbols["perf_top"] is not None:
        return read_pointer(pid, symbols["perf_start"]), read_pointer(pid, symbols["perf_top"])

    entry = read_pointer(pid, symbols["entry"])
    stride = read_pointer(pid, symbols["stride"])
    type_offset = read_pointer(pid, symbols["type_offset"])
    field_offset = read_pointer(pid, symbols["field_offset"])
    address_offset = read_pointer(pid, symbols["address_offset"])
    if entry == 0 or stride == 0:
        raise RuntimeError("Failed to read VMStructs")

    perf_start = None
    perf_top = None
    while True:
        type_ptr = read_pointer(pid, entry + type_offset)
        field_ptr = read_pointer(pid, entry + field_offset)
        if type_ptr == 0 or field_ptr == 0:
            break

        type_name = read_c_string(pid, type_ptr, 16)
        field_name = read_c_string(pid, field_ptr, 16)
        if type_name == b"PerfMemory":
            address_ptr = read_pointer(pid, entry + address_offset)
            target = read_pointer(pid, address_ptr)
            if field_name == b"_start":
                perf_start = target
            elif field_name == b"_top":
                perf_top = target
        entry += stride

    if perf_start is None or perf_top is None:
        raise RuntimeError("Failed to read VMStructs")
    return perf_start, perf_top


def parse_perf_data(blob: bytes):
    entry_offset = struct.unpack_from("<I", blob, 24)[0]
    num_entries = struct.unpack_from("<I", blob, 28)[0]
    counters = {}

    offset = entry_offset
    for _ in range(num_entries):
        entry_length, name_offset, vector_length, data_type, _, _, _, data_offset = struct.unpack_from(
            "<III4B I", blob, offset
        )
        name = blob[offset + name_offset:].split(b"\0", 1)[0].decode("utf-8", errors="ignore")
        if data_type == ord("J"):
            value = struct.unpack_from("<q", blob, offset + data_offset)[0]
            counters[name] = value
        offset += entry_length
    return counters


def percentage(used: int, capacity: int):
    if used < 0 or capacity < 0:
        return None
    if capacity == 0:
        return None if used == 0 else float("inf")
    return float(used) * 100.0 / float(capacity)


def get_gc_stat(pid: int):
    jvm_path, base_addr = locate_libjvm(pid)
    symbols = parse_elf_symbols(jvm_path, base_addr)
    perf_start, perf_top = read_vmstructs(pid, symbols)
    perf_data = process_vm_read(pid, perf_start, perf_top - perf_start)
    counters = parse_perf_data(perf_data)
    required = [
        GC_COUNTER_NAMES["s0u"],
        GC_COUNTER_NAMES["s0c"],
        GC_COUNTER_NAMES["s1u"],
        GC_COUNTER_NAMES["s1c"],
        GC_COUNTER_NAMES["eu"],
        GC_COUNTER_NAMES["ec"],
        GC_COUNTER_NAMES["ou"],
        GC_COUNTER_NAMES["oc"],
        GC_COUNTER_NAMES["mu"],
        GC_COUNTER_NAMES["mc"],
        GC_COUNTER_NAMES["ccsu"],
        GC_COUNTER_NAMES["ccsc"],
        GC_COUNTER_NAMES["ygc"],
        GC_COUNTER_NAMES["ygct"],
        GC_COUNTER_NAMES["fgc"],
        GC_COUNTER_NAMES["fgct"],
    ]
    missing = [name for name in required if name not in counters]
    if missing:
        raise RuntimeError(f"Missing required counter: {missing}")

    gc_stat = {
        "s0": percentage(counters[GC_COUNTER_NAMES["s0u"]], counters[GC_COUNTER_NAMES["s0c"]]),
        "s1": percentage(counters[GC_COUNTER_NAMES["s1u"]], counters[GC_COUNTER_NAMES["s1c"]]),
        "e": percentage(counters[GC_COUNTER_NAMES["eu"]], counters[GC_COUNTER_NAMES["ec"]]),
        "o": percentage(counters[GC_COUNTER_NAMES["ou"]], counters[GC_COUNTER_NAMES["oc"]]),
        "m": percentage(counters[GC_COUNTER_NAMES["mu"]], counters[GC_COUNTER_NAMES["mc"]]),
        "ccs": percentage(counters[GC_COUNTER_NAMES["ccsu"]], counters[GC_COUNTER_NAMES["ccsc"]]),
        "ygc": float(counters[GC_COUNTER_NAMES["ygc"]]),
        "ygct": float(counters[GC_COUNTER_NAMES["ygct"]]) / 1_000_000_000.0,
        "fgc": float(counters[GC_COUNTER_NAMES["fgc"]]),
        "fgct": float(counters[GC_COUNTER_NAMES["fgct"]]) / 1_000_000_000.0,
    }

    has_cgc = GC_COUNTER_NAMES["cgc"] in counters and GC_COUNTER_NAMES["cgct"] in counters
    if has_cgc:
        gc_stat["cgc"] = float(counters[GC_COUNTER_NAMES["cgc"]])
        gc_stat["cgct"] = float(counters[GC_COUNTER_NAMES["cgct"]]) / 1_000_000_000.0

    gc_stat["gct"] = gc_stat["ygct"] + gc_stat["fgct"] + (gc_stat.get("cgct", 0.0))
    return gc_stat


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1



def main():
    if len(sys.argv) != 2:
        return fail("Usage: hsperf_gcutil.py <pid>")

    try:
        pid = int(sys.argv[1])
        if pid <= 0:
            raise ValueError
    except ValueError:
        return fail("Usage: hsperf_gcutil.py <pid>")

    try:
        gc_stat = get_gc_stat(pid)
        print(gc_stat)
    except Exception as exc:
        return fail(str(exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
