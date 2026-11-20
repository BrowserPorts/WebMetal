#!/usr/bin/env python3
"""Generate host/src/format/table.rs from the WebMetal tables.

Inputs:
  tables/formats.toml    the Metal and WebGPU format lists and their pairings
  tables/limits.toml     the WebGPU feature list

The A10X's own format records come from the device profile the build is given (host/build.rs), not from this table.

Every capability cell of the WebGPU format rows is parsed into a closed set of forms; a cell in any other form stops
the generator, so a new kind of condition in the specification cannot be dropped silently.

Usage:
  formatgen.py            write table.rs
  formatgen.py --check    exit 1 if table.rs differs from what the inputs generate
"""

import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", "..", "..", ".."))
FORMATS = os.path.join(ROOT, "tables", "formats.toml")
LIMITS = os.path.join(ROOT, "tables", "limits.toml")
OUT = os.path.normpath(os.path.join(HERE, "..", "table.rs"))

FORMAT_CLASSES = {
    "same-format": "SameFormat",
    "no-counterpart": "NoCounterpart",
    "not-in-ios-17.7": "NotInIos17_7",
    "sentinel": "Sentinel",
    "stencil-aspect-view": "StencilAspectView",
}


class GenError(Exception):
    pass


def camel(name):
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[-_]", name))


def rust_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def cond(cell, features, where):
    """A capability cell: always, never, or with one feature."""
    if cell == "✓":
        return "Cond::Always"
    if cell in ("(empty)", "✗"):
        return "Cond::Never"
    m = re.fullmatch(r'If "([a-z0-9-]+)" is enabled', cell)
    if m:
        if m.group(1) not in features:
            raise GenError(f"{where}: unknown feature {m.group(1)!r}")
        return f"Cond::Feature(Feature::{camel(m.group(1))})"
    raise GenError(f"{where}: capability cell {cell!r} has no known form")


def sample_type(cell, features, where):
    forms = {
        '"float", "unfilterable-float"': "SampleType::Float",
        '"unfilterable-float"': "SampleType::UnfilterableFloat",
        '"uint"': "SampleType::Uint",
        '"sint"': "SampleType::Sint",
        '"depth", "unfilterable-float"': "SampleType::Depth",
    }
    if cell in forms:
        return forms[cell]
    m = re.fullmatch(r'"float" if "([a-z0-9-]+)" is enabled "unfilterable-float"', cell)
    if m:
        if m.group(1) not in features:
            raise GenError(f"{where}: unknown feature {m.group(1)!r}")
        return f"SampleType::FloatIf(Feature::{camel(m.group(1))})"
    raise GenError(f"{where}: sample type cell {cell!r} has no known form")


def required_feature(cell, features, where, quoted):
    if cell == "(empty)":
        return "None"
    name = cell[1:-1] if quoted else cell
    if quoted and not (cell.startswith('"') and cell.endswith('"')):
        raise GenError(f"{where}: feature cell {cell!r} is not quoted")
    if name not in features:
        raise GenError(f"{where}: unknown feature {name!r}")
    return f"Some(Feature::{camel(name)})"


def webgpu_rows(formats, features):
    rows = []
    for f in formats["webgpu_texture_format"]:
        name, table, aspects = f["name"], f["table"], f["aspects"]
        where = f"webgpu_texture_format {name}"
        r = {"name": name, "variant": camel(name), "table": table}
        if table == "plain":
            if len(aspects) != 1:
                raise GenError(f"{where}: a plain format has one row")
            a = aspects[0]
            r["feature"] = required_feature(a["Required Feature"], features, where, True)
            r["sample"] = sample_type(a["GPUTextureSampleType"], features, where)
            for key, cell in [
                ("render", "RENDER_ATTACHMENT"),
                ("blend", "blendable"),
                ("msaa", "multisampling"),
                ("resolve", "resolve"),
                ("storage_wo", 'STORAGE_BINDING "write-only"'),
                ("storage_ro", 'STORAGE_BINDING "read-only"'),
                ("storage_rw", 'STORAGE_BINDING "read-write"'),
            ]:
                r[key] = cond(a[cell], features, f"{where} {cell}")
            r["footprint"] = int(a["Texel block copy footprint (Bytes)"])
            r["block"] = (1, 1)
            # Plain color formats allow "3d" (26.1.1 lists no restriction); copies both ways.
            r["three_d"] = "Cond::Always"
            r["aspects"] = []
        elif table == "packed":
            if len(aspects) != 1:
                raise GenError(f"{where}: a packed format has one row")
            a = aspects[0]
            r["feature"] = required_feature(a["Feature"], features, where, False)
            r["sample"] = sample_type(a["GPUTextureSampleType"], features, where)
            # 26.1.3: packed formats are filterable, not renderable and not multisampled; no storage column.
            for key in ("render", "blend", "msaa", "resolve", "storage_wo", "storage_ro", "storage_rw"):
                r[key] = "Cond::Never"
            r["footprint"] = int(a["Texel block copy footprint (Bytes)"])
            m = re.fullmatch(r"(\d+) × (\d+)", a["Texel block width/height"])
            if not m:
                raise GenError(f"{where}: block size {a['Texel block width/height']!r}")
            r["block"] = (int(m.group(1)), int(m.group(2)))
            r["three_d"] = cond(a['"3d"'], features, f'{where} "3d"')
            r["aspects"] = []
        elif table == "depth-stencil":
            r["feature"] = "None"
            req = {"depth32float-stencil8": "depth32float-stencil8"}.get(name)
            if req:
                r["feature"] = f"Some(Feature::{camel(req)})"
            samples = []
            r["aspects"] = []
            for a in aspects:
                aw = f"{where} aspect {a['Aspect']}"
                fp = a["Texel block copy footprint (Bytes)"]
                r["aspects"].append(
                    {
                        "aspect": {"depth": "Aspect::Depth", "stencil": "Aspect::Stencil"}[a["Aspect"]],
                        "sample": sample_type(a["GPUTextureSampleType"], features, aw),
                        "copy_src": {"✓": "true", "✗": "false"}[a["Valid texel copy source"]],
                        "copy_dst": {"✓": "true", "✗": "false"}[a["Valid texel copy destination"]],
                        "footprint": "None" if fp == "–" else f"Some({int(fp)})",
                        "view": camel(a["Aspect-specific format"]),
                    }
                )
                samples.append(r["aspects"][-1]["sample"])
            r["sample"] = samples[0]
            # 26.1.2: depth-or-stencil formats support RENDER_ATTACHMENT and multisampling, not "3d".
            r["render"] = "Cond::Always"
            r["msaa"] = "Cond::Always"
            for key in ("blend", "resolve", "storage_wo", "storage_ro", "storage_rw"):
                r[key] = "Cond::Never"
            r["footprint"] = 0
            r["block"] = (1, 1)
            r["three_d"] = "Cond::Never"
        else:
            raise GenError(f"{where}: unknown table {table!r}")
        rows.append(r)
    return rows


def metal_rows(formats, wgpu_names):
    rows = []
    for f in formats["metal_pixel_format"]:
        where = f"metal_pixel_format {f['name']}"
        if f["class"] not in FORMAT_CLASSES:
            raise GenError(f"{where}: unknown class {f['class']!r}")
        webgpu = f.get("webgpu")
        if webgpu is not None and webgpu not in wgpu_names:
            raise GenError(f"{where}: unknown WebGPU format {webgpu!r}")
        rows.append(
            {
                "value": f["value"],
                "name": f["name"],
                "ios": f["ios_17_7"],
                "class": FORMAT_CLASSES[f["class"]],
                "webgpu": webgpu,
                "plan_row": f.get("plan_row"),
            }
        )
    return rows


def generate():
    with open(FORMATS, "rb") as fh:
        formats = tomllib.load(fh)
    with open(LIMITS, "rb") as fh:
        limits = tomllib.load(fh)
    features = [f["name"] for f in limits["feature"]]
    wg = webgpu_rows(formats, set(features))
    wg_names = {r["name"] for r in wg}
    mt = metal_rows(formats, wg_names)

    o = []
    w = o.append
    w("// Generated by host/src/format/gen/formatgen.py from tables/formats.toml and tables/limits.toml.")
    w("// Do not edit by hand.")
    w("")
    w("use super::webgpu::{Aspect, Cond, SampleType};")
    w("")
    w("/// The WebGPU features (`GPUFeatureName`, tables/limits.toml).")
    w("#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]")
    w("pub enum Feature {")
    for f in features:
        w(f"    {camel(f)},")
    w("}")
    w("")
    w("impl Feature {")
    w(f"    pub const ALL: [Feature; {len(features)}] = [")
    for f in features:
        w(f"        Feature::{camel(f)},")
    w("    ];")
    w("")
    w("    /// The `GPUFeatureName` string.")
    w("    pub fn name(self) -> &'static str {")
    w("        match self {")
    for f in features:
        w(f"            Feature::{camel(f)} => {rust_str(f)},")
    w("        }")
    w("    }")
    w("")
    w("    /// Features this one requires (WebGPU section 3.6.1.2); a device created with it also has them.")
    w("    pub fn requires(self) -> &'static [Feature] {")
    w("        match self {")
    for f in limits["feature"]:
        if f.get("requires"):
            deps = ", ".join(f"Feature::{camel(d)}" for d in f["requires"])
            w(f"            Feature::{camel(f['name'])} => &[{deps}],")
    w("            _ => &[],")
    w("        }")
    w("    }")
    w("}")
    w("")
    w("/// The WebGPU texture formats (`GPUTextureFormat`, tables/formats.toml).")
    w("#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]")
    w("pub enum WebGpuFormat {")
    for r in wg:
        w(f"    {r['variant']},")
    w("}")
    w("")
    w("impl WebGpuFormat {")
    w(f"    pub const ALL: [WebGpuFormat; {len(wg)}] = [")
    for r in wg:
        w(f"        WebGpuFormat::{r['variant']},")
    w("    ];")
    w("")
    w("    /// The `GPUTextureFormat` string.")
    w("    pub fn name(self) -> &'static str {")
    w("        WEBGPU_FORMATS[self as usize].name")
    w("    }")
    w("}")
    w("")
    w("/// Capabilities of each WebGPU format, indexed by [`WebGpuFormat`] (WebGPU section 26.1).")
    w(f"pub static WEBGPU_FORMATS: [super::webgpu::FormatCaps; {len(wg)}] = [")
    for r in wg:
        aspects = ", ".join(
            "super::webgpu::AspectCaps { "
            f"aspect: {a['aspect']}, sample: {a['sample']}, copy_src: {a['copy_src']}, copy_dst: {a['copy_dst']}, "
            f"footprint: {a['footprint']}, view: WebGpuFormat::{a['view']} }}"
            for a in r["aspects"]
        )
        w("    super::webgpu::FormatCaps {")
        w(f"        name: {rust_str(r['name'])},")
        w(f"        table: super::webgpu::Table::{camel(r['table'])},")
        w(f"        feature: {r['feature']},")
        w(f"        sample: {r['sample']},")
        for key in ("render", "blend", "msaa", "resolve", "storage_wo", "storage_ro", "storage_rw", "three_d"):
            w(f"        {key}: {r[key]},")
        w(f"        footprint: {r['footprint']},")
        w(f"        block: {r['block']},")
        w(f"        aspects: &[{aspects}],")
        w("    },")
    w("];")
    w("")
    w("/// The classification of a Metal pixel format in tables/formats.toml (`class`).")
    w("#[derive(Clone, Copy, Debug, PartialEq, Eq)]")
    w("pub enum FormatClass {")
    for v in FORMAT_CLASSES.values():
        w(f"    {v},")
    w("}")
    w("")
    w("/// A Metal pixel format row of tables/formats.toml.")
    w("#[derive(Clone, Copy, Debug, PartialEq, Eq)]")
    w("pub struct MetalFormatRow {")
    w("    pub value: u32,")
    w("    pub name: &'static str,")
    w("    /// Available in the iOS 17.7 SDK annotation.")
    w("    pub ios_17_7: bool,")
    w("    pub class: FormatClass,")
    w("    /// The same-format (or stencil-aspect) WebGPU counterpart.")
    w("    pub webgpu: Option<WebGpuFormat>,")
    w("    /// For a format without a counterpart, the handling row it belongs to (`plan_row` in tables/formats.toml).")
    w("    pub plan_row: Option<&'static str>,")
    w("}")
    w("")
    w(f"pub static METAL_FORMATS: [MetalFormatRow; {len(mt)}] = [")
    for r in sorted(mt, key=lambda r: r["value"]):
        webgpu = f"Some(WebGpuFormat::{camel(r['webgpu'])})" if r["webgpu"] else "None"
        plan = f"Some({rust_str(r['plan_row'])})" if r["plan_row"] else "None"
        w(
            f"    MetalFormatRow {{ value: {r['value']}, name: {rust_str(r['name'])}, ios_17_7: {str(r['ios']).lower()}, "
            f"class: FormatClass::{r['class']}, webgpu: {webgpu}, plan_row: {plan} }},"
        )
    w("];")
    return "\n".join(o) + "\n"


def main():
    try:
        text = generate()
    except GenError as e:
        print(f"formatgen: {e}", file=sys.stderr)
        return 2
    if "--check" in sys.argv[1:]:
        try:
            with open(OUT, encoding="utf-8") as fh:
                current = fh.read()
        except FileNotFoundError:
            current = None
        if current != text:
            print(f"formatgen: {os.path.relpath(OUT, ROOT)} is out of date", file=sys.stderr)
            return 1
        return 0
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
