# hsperf

Prints HotSpot perf counters, even when the target JVM is started with `-XX:+PerfDisableSharedMem` flag.
Unlike other similar utilities, it does **not** rely on access to `/tmp/hsperfdata_user` files.

Does not require JDK to run. Works with all versions of HotSpot JVM.

### Usage

```
hsperf <pid> [<counter>...]
```

If only `<pid>` is specified, the program prints all counters with their names.  
If a space separated list of counter names is given, the program prints values
of the specified counters, one value per line.

### How it works

1. Reads `/proc/[pid]/maps` to find the location and the base address of `libjvm.so`.
2. Parses `libjvm.so` to get the address of `PerfData` structure.
3. Calls [`process_vm_readv`](https://man7.org/linux/man-pages/man2/process_vm_readv.2.html)
   to read `PerfData` of the target JVM.

If `libjvm.so` does not contain debug symbols, the program gets the address of
`VMStructs` instead (which is always available) and then looks for `PerfData`
addresses using `VMStructs`.

### Supported OS

Linux 3.2+ 64-bit


# gcutil.py

用Python来采集jvm的gc指标，类似于 jstat -gcutil pid，适用缺少jstat命令的jre运行环境

适用于 Linux 64 bit, Aarch64 & X86_64 OS

### Usage

```
# ./gcutil.py 1861925
{'s0': 0.0, 's1': 99.9997456874957, 'e': 29.166657394835983, 'o': 1.0181403245726859, 'm': 94.42428739944307, 'ccs': 86.13516000600961, 'ygc': 2.0, 'ygct': 0.255731984, 'fgc': 0.0, 'fgct': 0.0, 'gct': 0.255731984}


# /opt/jdk8/bin/jstat -gcutil 1861925
  S0     S1     E      O      M     CCS    YGC     YGCT    FGC    FGCT     GCT
  0.00 100.00  29.17   1.02  94.42  86.14      2    0.256     0    0.000    0.256

# /opt/jdk21/bin/jstat -gcutil 1861925
  S0     S1     E      O      M     CCS    YGC     YGCT     FGC    FGCT     CGC    CGCT       GCT
  0.00 100.00  29.17   1.02  94.42  86.14      2     0.256     0     0.000     -         -     0.256

```
