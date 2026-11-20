# WebMetal

WebMetal is an implementation of Metal on WebGPU. A Metal driver plugin in
the guest turns an application's Metal calls into a wire protocol, and a host
executor replays that protocol on one WebGPU device: the browser's, or a
native implementation's.

It is the GPU of the web device that
[unixnu-gex](https://github.com/BrowserPorts/unixnu-gex) runs, and it is a
driver of [UniXNU](https://github.com/BrowserPorts/UniXNU).

- Used by: [unixnu-gex](https://github.com/BrowserPorts/unixnu-gex), as its
  `third_party/WebMetal` submodule, and
  [UniXNU](https://github.com/BrowserPorts/UniXNU) (branch `webmetal`), as
  `drivers/WebMetal`
- Depends on: [AirWgsl](https://github.com/BrowserPorts/AirWgsl), the
  compiler that turns Metal's shaders into WGSL

## How it works

Metal loads a driver for the device it is given. WebMetal's guest plugin is
that driver. It carries the names of the A10X driver bundle it takes the
place of, `AGXMetalA10.bundle`, so that Metal.framework loads it, and it
reports the A10X's capabilities. Each call becomes a record of the wire
protocol, on one of three channels:

- **control**: requests from guest to host, each answered by one reply
- **stream**: the command-buffer stream, submitted by a commit
- **event**: records from host to guest

The host executor decodes the records, validates them against Metal's rules
and replays them on WebGPU. Shaders arrive as AIR, Metal's LLVM bitcode, and
AirWgsl compiles them to WGSL.

Metal behaviour with no exact WebGPU or WGSL form is refused loudly: the
Metal error where Metal defines one, otherwise a report that names what is
not implemented. Nothing is answered with a substitute. `tables/refusals.md`
lists those cases, each with the clause of the specification that blocks it.

## Layout

| path | what it is |
|------|------------|
| `guest/` | the guest Metal plugin, in Objective-C++: the device, resources, command buffers and encoders, shaders and pipelines. `guest/gen/` holds its generators |
| `wire/` | the wire protocol: its schema, the generator, the generated C++ header for the guest, and the `webmetal-wire` crate. `wire/README.md` gives the format |
| `host/` | the `webmetal-host` crate, the executor |
| `host/embed-ref/` | a reference embedder, which runs the executor without a kernel and replays recordings |
| `host/web-tests/` | the executor's tests built as browser modules |
| `profile/` | the tools that extract the device profile (the answers of the Metal device WebMetal presents) and the driver's texture format table from your own root. The profile is not in the repository: `WEBMETAL_PROFILE` names your copy when the host builds, and `host/src/format/profile.rs` describes its fields |
| `tables/` | the specification tables (formats, limits, refusals), each row with its source, `check.py`, which verifies them, and in `measure/` the programs that take the measurements some rows cite |
| `tests/reference/` | `wmref`, small Objective-C programs that record what real Metal does, as the reference the executor is tested against |

## Building

Data extracted from the A10X's system software is not part of the
repository. What the build needs of the A10X and its driver, you supply from
your own device:

- `WEBMETAL_PROFILE`: the path of the device profile.
- `WEBMETAL_AGX_TEXTURE_FORMATS`: the path of the A10X driver's texture
  format table, in the Rust form `host/src/mem/agx/mod.rs` describes.
- For the guest plugin, `guest/Generated/`: the target surface and the
  format table's C++ form. `guest/Plugin/WebMetalSurface.h` lists them;
  `guest/gen/profilegetters.py` writes the profile's getters there and
  `guest/Commands/gen/encodergen.py` the encoder records next to their
  sources. The plugin's build takes the driver bundle's `Info.plist` and
  resource rules from your root.

The build fails, naming what is missing, when any of them is absent.

What real Metal did on your own Mac is not part of the repository either. The
tests that compare with it read it from where you name it when the host is
built, and are ignored, saying so, without it:

- `WEBMETAL_REFERENCE`: a run of the reference harness (`tests/reference`,
  `wmref run --out RUN`; its README shows how to record one).
- `WEBMETAL_MEASUREMENTS`: the directory of measurement records the programs
  in `tables/measure/` write; `tables/check.py --measurements` reads it too.

The Rust side is one Cargo workspace:

```sh
WEBMETAL_PROFILE=<profile.toml> WEBMETAL_AGX_TEXTURE_FORMATS=<formats.rs> cargo build --release
```

The executor has two bindings to WebGPU, each a feature of `webmetal-host`:

- `native` binds the `webgpu.h` C API of a native implementation, chosen at
  build time. `WEBGPU_NATIVE` is `dawn` or `wgpu-native`, and
  `WEBGPU_NATIVE_DIR` is its install root, holding `include/webgpu/webgpu.h`
  and `lib/`. The build fails when the feature is on and either is missing.
- `web` binds the browser's WebGPU, for wasm32. The `wasm-bindgen` CLI that
  post-processes the module must be the version `host/Cargo.toml` pins.

The guest plugin is built by UniXNU's `scripts/build-web-metal-plugin.py`, on
a Mac with Xcode and its iOS SDK. It writes `AGXMetalA10.bundle` and a macOS
test shim that runs the same classes on a Mac.

The reference harness builds and runs on macOS 14 or newer with Xcode's Metal
toolchain:

```sh
cd tests/reference
python3 build.py
build/macos/wmref list
build/macos/wmref run --out RUN
```

`tests/reference/README.md` covers it.

## Making the device data

The tools take your own root file system and write where you tell them, by
default to paths Git ignores. Each takes `--help`. They were checked on
iPadOS 17.7.11 (21H461) for the A10X (`AGXMetalA10`, `AGXA10XDevice`); the
root, its build and the driver are arguments. They need Python 3.11 or newer.

- The device profile: UniXNU's `scripts/audit-target-metal-profile.py` runs
  `profile/webmetal_profile` (with the `unicorn` package and LLVM's
  `llvm-mc`) and writes `profile/device-profile.toml`, for
  `WEBMETAL_PROFILE`. `python3 -m unittest discover -s profile/tests` checks
  the package, and the profile `WEBMETAL_PROFILE` names.
- The texture format table, from `profile/`:

  ```sh
  python3 -B -m webmetal_profile.texture_formats <root>/System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64 \
      --driver AGXMetalA10 --build "iPadOS 17.7.11, 21H461"
  ```

  It writes `guest/Generated/WebMetalTextureFormats.inc` and
  `guest/Generated/agx-texture-formats.rs`, for
  `WEBMETAL_AGX_TEXTURE_FORMATS`.
- The target surface and the system clients' lists: UniXNU's
  `scripts/audit-target-web-metal-surface.py` writes `guest/Generated/` and
  `guest/SPI/clients/`.
- The QuartzCore driver-selector list and the check of the wire schema
  against the binaries: `wire/tools/target_surface.py --root <root> --driver
  /System/Library/Extensions/AGXMetalA10.bundle/AGXMetalA10 --build 21H461
  derive --write` (then `check`); `wire/README.md` describes the check.

## Using it

An embedder gives the executor what a kernel would: the guest's memory, its
surfaces and somewhere to present frames. That interface is
`webmetal_host::embed`.

- unixnu-gex's `gexc`, built with its `webmetal` feature, runs the executor
  on a host thread over a native WebGPU implementation. Its page runs the
  same executor in a Worker on the browser's WebGPU.
- `host/embed-ref` runs it with no guest at all, for tests.

## Licence

GNU Affero General Public License, version 3 only (`LICENSE`). `NOTICE` lists
the code adapted from other projects and their licences.

## Contributing

This repository takes no pull requests and no issues. Both are turned off
for the BrowserPorts organisation.
