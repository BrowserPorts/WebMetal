"""Write the fields of the AGX G9 driver's texture format table that the TextureGen2 layout ports read (the guest's,
guest/Resources/WebMetalTextureLayout.mm, and the host's, host/src/mem/agx, which also reads and writes texture memory):
WebMetalTextureFormats.inc for the guest and its Rust form for the host (WEBMETAL_AGX_TEXTURE_FORMATS).

The input is the user's own dyld shared cache and the name of its AGX driver image. Everything is found in that cache:
AGX::G9::TextureFormatTable::chooseTextureFormatOBJC by its symbol, its jump table decoded statically (every case is
`adrp x0; add x0; ret`); the block-dimension and class-word tables in the TextureGen2 constructor, and the colour-space
conversion table in the AGXTextureLayout initializer, by the shape of the code that indexes them. A shape that is not
found stops the tool. Only named fields are emitted, never raw entry bytes. Checked on AGXMetalA10 of iPadOS 17.7.11
(21H461).

usage (from profile/):
  python3 -B -m webmetal_profile.texture_formats CACHE --driver IMAGE --build LABEL [--inc PATH] [--rs PATH]

CACHE is the root's System/Library/Caches/com.apple.dyld/dyld_shared_cache_arm64; IMAGE the driver's image name (for
example AGXMetalA10); LABEL the build as the outputs name it (for example "iPadOS 17.7.11, 21H461"). The outputs default
to guest/Generated/WebMetalTextureFormats.inc and guest/Generated/agx-texture-formats.rs, which Git ignores.
"""
import argparse
import struct
from pathlib import Path

from .dyld import Cache

CHOOSE_SYMBOL = '__ZNK3AGX2G918TextureFormatTable23chooseTextureFormatOBJCE14MTLPixelFormat'
TEXTURE_GEN2_CTOR = '__ZN3AGX11TextureGen2IL22AGXTextureMemoryLayout1ENS_2G98EncodersENS2_7ClassesEEC2E'
LAYOUT_INIT = '-[AGXTextureLayout initWithDevice:descriptor:isHeapOrBufferBacked:]'
UNSUPPORTED_NAME = '_texFormatUnsupported'
GENERATED = Path(__file__).resolve().parents[2] / 'guest' / 'Generated'


def fail(msg):
    raise SystemExit('texture_formats: ' + msg)


def insn(c, a):
    return struct.unpack('<I', c.read(a, 4))[0]


def adrp(c, a, rd):
    i = insn(c, a)
    if (i & 0x9f000000) != 0x90000000 or (i & 0x1f) != rd:
        fail(f'expected adrp x{rd} at {a:#x}, found {i:#010x}')
    imm = (((i >> 5) & 0x7ffff) << 2) | ((i >> 29) & 3)
    if imm & (1 << 20):
        imm -= 1 << 21
    return (a & ~0xfff) + (imm << 12)


def add_imm(c, a, rd, rn):
    i = insn(c, a)
    if (i & 0xff800000) != 0x91000000 or (i & 0x1f) != rd or ((i >> 5) & 0x1f) != rn:
        fail(f'expected add x{rd}, x{rn}, #imm at {a:#x}, found {i:#010x}')
    imm = (i >> 10) & 0xfff
    return imm << 12 if (i >> 22) & 1 else imm


def adr(c, a, rd):
    i = insn(c, a)
    if (i & 0x9f000000) != 0x10000000 or (i & 0x1f) != rd:
        fail(f'expected adr x{rd} at {a:#x}, found {i:#010x}')
    imm = (((i >> 5) & 0x7ffff) << 2) | ((i >> 29) & 3)
    if imm & (1 << 20):
        imm -= 1 << 21
    return a + imm


def load_x0(c, a, x0=None):
    """A jump-table case returns x0: `adrp x0; add x0, x0, #imm; ret`, or a bare `ret` that returns the value the
    function loaded into x0 before the branch."""
    if insn(c, a) == 0xd65f03c0:
        if x0 is None:
            fail(f'bare ret at {a:#x} without a preset x0')
        return x0
    page = adrp(c, a, 0)
    target = page + add_imm(c, a + 4, 0, 0)
    if insn(c, a + 8) != 0xd65f03c0:
        fail(f'expected ret at {a + 8:#x}')
    return target


def sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def page_value(c, a):
    """(Rd, value) of `adrp xd; add xd, xd, #imm` at a, else None."""
    i, j = insn(c, a), insn(c, a + 4)
    if (i & 0x9f000000) != 0x90000000 or (j & 0xff800000) != 0x91000000:
        return None
    rd = i & 0x1f
    if (j & 0x1f) != rd or ((j >> 5) & 0x1f) != rd:
        return None
    page = (a & ~0xfff) + (sext((((i >> 5) & 0x7ffff) << 2) | ((i >> 29) & 3), 21) << 12)
    imm = (j >> 10) & 0xfff
    return rd, page + (imm << 12 if (j >> 22) & 1 else imm)


def function_range(image, start):
    following = [a for a in image.symbols.values() if a > start]
    if not following:
        fail(f'no symbol after {start:#x}')
    return start, min(following)


def symbol_starting(image, prefix):
    found = sorted(a for n, a in image.symbols.items() if n.startswith(prefix))
    if len(found) != 1:
        fail(f'expected one symbol starting {prefix}, found {len(found)}')
    return found[0]


def cmp_imm(w):
    """(Rn, imm) of CMP (SUBS to the zero register, immediate), else None."""
    if (w & 0x7f80001f) != 0x7100001f:
        return None
    imm = (w >> 10) & 0xfff
    return (w >> 5) & 0x1f, imm << 12 if (w >> 22) & 1 else imm


def sub_imm(w):
    """(Rd, Rn, imm) of SUB (immediate), else None."""
    if (w & 0x7f800000) != 0x51000000:
        return None
    return w & 0x1f, (w >> 5) & 0x1f, (w >> 10) & 0xfff


def ldr_reg(w):
    """(Rt, Rn, Rm, size log2, shift) of LDR (register) of a W, X or D register, else None."""
    if (w & 0xbfe00c00) == 0xb8600800:             # LDR Wt/Xt, [Xn, Rm{, extend #s}]
        size = 3 if w >> 30 & 1 else 2
    elif (w & 0xffe00c00) == 0xfc600800:           # LDR Dt, [Xn, Rm{, extend #s}]
        size = 3
    else:
        return None
    return w & 0x1f, (w >> 5) & 0x1f, (w >> 16) & 0x1f, size, (w >> 12) & 1


def ldr_imm32(w):
    """(Rt, Rn, offset) of LDR Wt, [Xn, #imm], else None."""
    if (w & 0xffc00000) != 0xb9400000:
        return None
    return w & 0x1f, (w >> 5) & 0x1f, ((w >> 10) & 0xfff) * 4


def mov_wide32(c, a):
    """The value `mov wd, #lo; movk wd, #hi, lsl #16` at a builds, with Rd, else None."""
    i, j = insn(c, a), insn(c, a + 4)
    if (i & 0xffe00000) != 0x52800000 or (j & 0xffe00000) != 0x72a00000 or (i & 0x1f) != (j & 0x1f):
        return None
    return i & 0x1f, ((i >> 5) & 0xffff) | (((j >> 5) & 0xffff) << 16)


def bounded_tables(c, start, end, bias):
    """Tables indexed as `sub r, r, #bias; cmp r, #limit; b.hi ...; adrp t; add t; ldr w, [t, r, ...]`, with the
    value of the first `mov w; movk w` between the comparison and the table: [(table, limit + 1, mask or None)]."""
    out = []
    for a in range(start, end - 8, 4):
        s = sub_imm(insn(c, a))
        k = cmp_imm(insn(c, a + 4))
        if not s or not k or s[0] != s[1] or s[2] != bias or k[0] != s[0]:
            continue
        if (insn(c, a + 8) & 0xff00001f) != 0x54000008:       # b.hi
            continue
        mask = None
        for b in range(a + 12, min(a + 64, end), 4):
            m = mov_wide32(c, b)
            if m and mask is None:
                mask = m[1]
            t = page_value(c, b)
            if t:
                load = ldr_reg(insn(c, b + 8))
                if load and load[1] == t[0] and load[2] == s[0] and load[3] == 2:
                    out.append((t[1], k[1] + 1, mask))
                break
    return out


def find_class_words(c, image):
    """The class-word table of the TextureGen2 constructor: indexed by the entry's class (+0x20) less 2, where the
    constructor's mask has that bit set."""
    start, end = function_range(image, symbol_starting(image, TEXTURE_GEN2_CTOR))
    found = [t for t in bounded_tables(c, start, end, 2) if t[2] is not None]
    if len(found) != 1:
        fail(f'the TextureGen2 constructor indexes {len(found)} masked class tables, not one')
    return found[0]


def find_block_dims(c, image):
    """The block-dimension table of the TextureGen2 constructor: (width, height) pairs of u32, indexed by the entry's
    +0x38 word."""
    start, end = function_range(image, symbol_starting(image, TEXTURE_GEN2_CTOR))
    found = []
    for a in range(start, end - 12, 4):
        idx = ldr_imm32(insn(c, a))
        t = page_value(c, a + 4)
        load = ldr_reg(insn(c, a + 12)) if t else None
        if idx and idx[2] == 0x38 and t and load and load[1] == t[0] and load[2] == idx[0] and load[3] == 3 \
                and load[4]:
            found.append(t[1])
    if len(set(found)) != 1:
        fail(f'the TextureGen2 constructor indexes {len(set(found))} block-dimension tables, not one')
    return found[0]


def find_color_conversions(c, image):
    """The colour-space conversion table of the AGXTextureLayout initializer: indexed by the descriptor's
    colorSpaceConversion less 1."""
    found = bounded_tables(c, *function_range(image, image.symbols[LAYOUT_INIT]), 1)
    if len(found) != 1:
        fail(f'the AGXTextureLayout initializer indexes {len(found)} conversion tables, not one')
    return found[0][:2]


def decode_choose(c, CHOOSE):
    # sub w8, w0, #0x1 ; cmp w8, #limit ; b.hi default
    if insn(c, CHOOSE) != 0x51000408:
        fail('chooseTextureFormatOBJC does not start with sub w8, w0, #1')
    cmp = insn(c, CHOOSE + 4)
    if (cmp & 0xffc003ff) != 0x7100011f:
        fail('expected cmp w8, #imm')
    limit = (cmp >> 10) & 0xfff
    bhi = insn(c, CHOOSE + 8)
    if (bhi & 0xff00001f) != 0x54000008:
        fail('expected b.hi')
    off = (bhi >> 5) & 0x7ffff
    if off & (1 << 18):
        off -= 1 << 19
    default = load_x0(c, CHOOSE + 8 + off * 4)
    # adrp x0; add x0 (the value a bare `ret` case returns); adrp x9; add x9 (table); adr x10 (base);
    # ldrh w11, [x9, x8, lsl #1]
    preset = adrp(c, CHOOSE + 0xc, 0) + add_imm(c, CHOOSE + 0x10, 0, 0)
    table = adrp(c, CHOOSE + 0x14, 9) + add_imm(c, CHOOSE + 0x18, 9, 9)
    base = adr(c, CHOOSE + 0x1c, 10)
    if insn(c, CHOOSE + 0x20) != 0x7868792b:
        fail('expected ldrh w11, [x9, x8, lsl #1]')
    return limit, default, table, base, preset


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cache', help="the root's dyld_shared_cache_arm64")
    ap.add_argument('--driver', required=True, help='the AGX driver image, for example AGXMetalA10')
    ap.add_argument('--build', required=True, help='the build as the outputs name it, for example "iPadOS 17.7.11, 21H461"')
    ap.add_argument('--inc', default=str(GENERATED / 'WebMetalTextureFormats.inc'), help='the guest output')
    ap.add_argument('--rs', default=str(GENERATED / 'agx-texture-formats.rs'), help='the host output')
    args = ap.parse_args()
    out_path, rust_path = args.inc, args.rs
    c = Cache(args.cache)
    image = c.image(args.driver)
    if CHOOSE_SYMBOL not in image.symbols:
        fail(f'{args.driver} has no {CHOOSE_SYMBOL}')
    CHOOSE = image.symbols[CHOOSE_SYMBOL]
    CLASS_WORDS, CLASS_WORDS_COUNT, CLASS_WORDS_MASK = find_class_words(c, image)
    BLOCK_DIMS = find_block_dims(c, image)
    COLOR_CONVERSIONS, COLOR_CONVERSIONS_COUNT = find_color_conversions(c, image)
    origin = f'{args.driver} ({args.build})'
    limit, default, table, base, preset = decode_choose(c, CHOOSE)
    sym = c.symbolize(default)
    if not sym or sym[1] != UNSUPPORTED_NAME or sym[2] != 0:
        fail(f'default case {default:#x} is not {UNSUPPORTED_NAME}')
    entries = {}
    for pf in range(1, limit + 2):           # w0 - 1 <= limit
        v = struct.unpack('<H', c.read(table + 2 * (pf - 1), 2))[0]
        entries[pf] = load_x0(c, base + 4 * v, preset)
    block_dims = []
    rows = []

    def fields(addr):
        e = c.read(addr, 0x60)
        planes = struct.unpack_from('<I', e, 0x18)[0]
        index = struct.unpack_from('<I', e, 0x38)[0]
        return dict(planes=planes,
                    cls=struct.unpack_from('<I', e, 0x20)[0],
                    alignLog2=e[0x30],
                    bytesPerPixel=e[0x31],
                    blockIndex=index,
                    depthStencil=1 if struct.unpack_from('<I', e, 0x3c)[0] & 0xc else 0,
                    mipmapGeneration=1 if struct.unpack_from('<I', e, 0x3c)[0] & 0x3 else 0,
                    chroma=struct.unpack_from('<I', e, 0x48)[0],
                    planesWithoutSurface=struct.unpack_from('<I', e, 0x50)[0],
                    planeBytes=(e[0x58], e[0x59]))

    unsupported = fields(default)
    supported = {}
    for pf, addr in sorted(entries.items()):
        if addr == default:
            continue
        f = fields(addr)
        if f['planes'] > 2:
            fail(f'pixel format {pf}: {f["planes"]} planes; the port models at most 2 twiddled block infos')
        s = c.symbolize(addr)
        f['entry'] = f'{s[1]}+{s[2]:#x}' if s and s[1] else f'{addr:#x}'
        supported[pf] = f
    max_index = max(f['blockIndex'] for f in list(supported.values()) + [unsupported])
    dims = [struct.unpack('<2I', c.read(BLOCK_DIMS + 8 * i, 8)) for i in range(max_index + 1)]
    class_words = struct.unpack(f'<{CLASS_WORDS_COUNT}I', c.read(CLASS_WORDS, 4 * CLASS_WORDS_COUNT))
    conversions = struct.unpack(f'<{COLOR_CONVERSIONS_COUNT}I', c.read(COLOR_CONVERSIONS, 4 * COLOR_CONVERSIONS_COUNT))

    def row(pf, f):
        bw, bh = dims[f['blockIndex']]
        return (f'    {{{pf}, {f["planes"]}, {f["cls"]}, {f["alignLog2"]}, {f["bytesPerPixel"]}, {bw}, {bh}, '
                f'{"true" if f["depthStencil"] else "false"}, {f["chroma"]}, {f["planesWithoutSurface"]}, '
                f'{{{f["planeBytes"][0]}, {f["planeBytes"][1]}}}, {"true" if f["mipmapGeneration"] else "false"}}},')

    out = []
    out.append('// Generated by profile/webmetal_profile/texture_formats.py from ' + origin + '. Do not edit.')
    out.append(f'// Source: AGX::G9::TextureFormatTable::chooseTextureFormatOBJC ({CHOOSE:#x}); entry fields +0x18 planes,')
    out.append('// +0x20 class, +0x30 alignment log2, +0x31 bytes per pixel, +0x38 block-dimension index (resolved through')
    out.append(f'// {BLOCK_DIMS:#x}), +0x3c & 0xc depth/stencil, +0x48 chroma subsampling, +0x50 planes usable without an')
    out.append('// IOSurface, +0x58/+0x59 bytes per block of planes 0 and 1, +0x3c & 0x3 nonzero (the entries whose levels')
    out.append('// generateMipmapsForTexture: fills).')
    out.append(f'// chooseTextureFormatOBJC maps pixel formats 1..{limit + 1} through its table; every other value, and every')
    out.append(f'// format not listed here, maps to {UNSUPPORTED_NAME}.')
    out.append('')
    out.append(f'constexpr uint32_t kChooseTextureFormatLimit = {limit};  // `cmp w8, #{limit:#x}` on pixelFormat - 1')
    out.append('')
    out.append(f'// {UNSUPPORTED_NAME} ({default:#x})')
    out.append('constexpr FormatInfo kUnsupportedFormat = ' + row(0, unsupported)[4:-1] + ';')
    out.append('')
    out.append('constexpr FormatInfo kFormats[] = {')
    for pf, f in sorted(supported.items()):
        out.append(row(pf, f) + f'  // {f["entry"]}')
    out.append('};')
    out.append('')
    out.append(f'// {CLASS_WORDS:#x}: TextureGen2 this+0x60, indexed by entry class - 2 where bit (class - 2) of {CLASS_WORDS_MASK:#x} is set')
    out.append(f'constexpr uint32_t kClassWordMask = {CLASS_WORDS_MASK:#x};')
    out.append('constexpr uint32_t kClassWords[' + str(CLASS_WORDS_COUNT) + '] = {' + ', '.join(map(str, class_words)) + '};')
    out.append('')
    out.append(f'// {COLOR_CONVERSIONS:#x}: eAGXColorSpaceConversion for MTLTextureDescriptor colorSpaceConversion 1..{COLOR_CONVERSIONS_COUNT} (callers of the ctor)')
    out.append('constexpr uint32_t kColorSpaceConversions[' + str(COLOR_CONVERSIONS_COUNT) + '] = {' + ', '.join(map(str, conversions)) + '};')
    out.append('')
    with open(out_path, 'w') as fh:
        fh.write('\n'.join(out))
    print(f'{len(supported)} supported formats, block-dimension indices 0..{max_index}, written to {out_path}')
    if rust_path:
        with open(rust_path, 'w') as fh:
            fh.write(rust(limit, unsupported, supported, dims, class_words, conversions, origin, CHOOSE, BLOCK_DIMS,
                          default, CLASS_WORDS, CLASS_WORDS_MASK, COLOR_CONVERSIONS))
        print(f'written to {rust_path}')


def rust(limit, unsupported, supported, dims, class_words, conversions, origin, CHOOSE, BLOCK_DIMS, default, CLASS_WORDS,
         CLASS_WORDS_MASK, COLOR_CONVERSIONS):
    CLASS_WORDS_COUNT, COLOR_CONVERSIONS_COUNT = len(class_words), len(conversions)
    def row(pf, f):
        bw, bh = dims[f['blockIndex']]
        return (f'FormatInfo {{ pixel_format: {pf}, planes: {f["planes"]}, cls: {f["cls"]}, align_log2: {f["alignLog2"]}, '
                f'bytes_per_pixel: {f["bytesPerPixel"]}, block_width: {bw}, block_height: {bh}, '
                f'depth_stencil: {"true" if f["depthStencil"] else "false"}, chroma: {f["chroma"]}, '
                f'planes_without_surface: {f["planesWithoutSurface"]}, '
                f'plane_bytes: [{f["planeBytes"][0]}, {f["planeBytes"][1]}], '
                f'mipmap_generation: {"true" if f["mipmapGeneration"] else "false"} }}')

    out = []
    out.append('//! Generated by profile/webmetal_profile/texture_formats.py from ' + origin + '. Do not edit.')
    out.append('//!')
    out.append(f'//! Source: `AGX::G9::TextureFormatTable::chooseTextureFormatOBJC` ({CHOOSE:#x}); entry fields +0x18 planes, +0x20')
    out.append(f'//! class, +0x30 alignment log2, +0x31 bytes per pixel, +0x38 block-dimension index (resolved through {BLOCK_DIMS:#x}),')
    out.append('//! +0x3c & 0xc depth/stencil, +0x48 chroma subsampling, +0x50 planes usable without an IOSurface, +0x58/+0x59 bytes')
    out.append('//! per block of planes 0 and 1, +0x3c & 0x3 nonzero (the entries whose levels `generateMipmapsForTexture:` fills).')
    out.append(f'//! `chooseTextureFormatOBJC` maps pixel formats 1..{limit + 1} through its table; every other value, and every format')
    out.append(f'//! not listed here, maps to `{UNSUPPORTED_NAME}`.')
    out.append('')
    out.append('use super::gen2::FormatInfo;')
    out.append('')
    out.append(f'/// `cmp w8, #{limit:#x}` on pixelFormat - 1.')
    out.append(f'pub const CHOOSE_TEXTURE_FORMAT_LIMIT: u32 = {limit};')
    out.append('')
    out.append(f'/// `{UNSUPPORTED_NAME}` ({default:#x}).')
    out.append('#[rustfmt::skip]')
    out.append('pub static UNSUPPORTED_FORMAT: FormatInfo = ' + row(0, unsupported) + ';')
    out.append('')
    out.append('/// The supported entries, by pixel format.')
    out.append('#[rustfmt::skip]')
    out.append('pub static FORMATS: [FormatInfo; ' + str(len(supported)) + '] = [')
    for pf, f in sorted(supported.items()):
        out.append('    ' + row(pf, f) + f',  // {f["entry"]}')
    out.append('];')
    out.append('')
    out.append(f'/// {CLASS_WORDS:#x}: TextureGen2 this+0x60, indexed by entry class - 2 where bit (class - 2) of {CLASS_WORDS_MASK:#x} is set.')
    out.append(f'pub const CLASS_WORD_MASK: u32 = {CLASS_WORDS_MASK:#x};')
    out.append(f'pub const CLASS_WORDS: [u32; {CLASS_WORDS_COUNT}] = [' + ', '.join(map(str, class_words)) + '];')
    out.append('')
    out.append(f'/// {COLOR_CONVERSIONS:#x}: eAGXColorSpaceConversion for MTLTextureDescriptor colorSpaceConversion 1..{COLOR_CONVERSIONS_COUNT} (callers of the ctor).')
    out.append(f'pub const COLOR_SPACE_CONVERSIONS: [u32; {COLOR_CONVERSIONS_COUNT}] = [' + ', '.join(map(str, conversions)) + '];')
    out.append('')
    return '\n'.join(out)


if __name__ == '__main__':
    main()
