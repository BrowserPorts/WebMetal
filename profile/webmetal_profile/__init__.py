"""Extraction of a Metal device profile from the user's own root (checked on iPadOS 17.7.11, 21H461, for the A10X).

`build.collect` runs the primary method (`target`: the target's own Metal driver code under
emulation) and the cross-checks (`host_probe`: this Mac's Metal.framework; `static_decode`:
LLVM's disassembler; `sampler`: the host Metal compiler); `render.render` writes the TOML
profile. UniXNU's scripts/audit-target-metal-profile.py is the entry point.
"""
