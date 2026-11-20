#!/usr/bin/env python3
"""Generate guest/Generated/WebMetalProfileGetters.mm from the device profile the user supplies.

The A10X's capability getters answer what the device profile records (host/src/format/profile.rs describes the
profile). Each driver selector of the device's classes (AGXA10XDevice and AGXA10FamilyDevice, the rows of
guest/Generated/WebMetalTargetSurface.h) whose answer the profile holds becomes a method of a category on the class
that carries it:

  [getters], [driver_getters]   a zero-argument getter returns the recorded value;
  [queries."<selector>"]        a one-integer-argument query returns the recorded value for each argument of its
                                contiguous profiled domain and is LOUD for any other argument; a query the profile
                                lists in [query_failures] is left to hand-written code;
  [heap]                        heapBufferSizeAndAlignWithLength:options: returns the length as the size and the
                                recorded alignment (buffer_size_is_length must be true).

The comment above each method is the profile's provenance for the value (the comment after it in the profile). A value
whose type does not match the selector's type encoding, or a selector whose encoding is not one of the shapes above,
stops the generator; nothing is guessed.

Usage:
  profilegetters.py [--profile PATH] [--out PATH]

--profile defaults to $WEBMETAL_PROFILE; --out to guest/Generated/WebMetalProfileGetters.mm.
"""
import argparse
import os
import re
import sys
import textwrap
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SURFACE = ROOT / 'guest/Generated/WebMetalTargetSurface.h'
OUTPUT = ROOT / 'guest/Generated/WebMetalProfileGetters.mm'
# The class the device profile describes, then the classes of its chain inside the bundle, most derived first.
DEVICE_CHAIN = ('AGXA10XDevice', 'AGXA10FamilyDevice')

CTYPES = {'B': 'BOOL', 'C': 'uint8_t', 'c': 'int8_t', 'S': 'uint16_t', 's': 'int16_t', 'I': 'uint32_t', 'i': 'int32_t',
          'Q': 'NSUInteger', 'q': 'NSInteger', 'L': 'uint64_t', 'l': 'int64_t', 'f': 'float', 'd': 'double',
          '{?=QQQ}': 'MTLSize', '{?=QQ}': 'MTLSizeAndAlign'}
BITFIELDS = re.compile(r'^\{(\w+)=((?:b\d+)+)\}$')
ROW = re.compile(r'\{SelectorKind::(Instance|Class), "((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)", '
                 r'"((?:[^"\\]|\\.)*)", "((?:[^"\\]|\\.)*)"\}')


def fail(message):
    raise SystemExit('profilegetters: ' + message)


def parse_type(text, at):
    """One Objective-C (extended) type encoding starting at `at`: (encoding, next index)."""
    start = at
    while text[at] in 'rnNoORV':  # qualifiers: const, in, inout, out, bycopy, byref, oneway
        at += 1
    ch = text[at]
    if ch == '@':
        at += 1
        if at < len(text) and text[at] == '?':
            at += 1
            if at < len(text) and text[at] == '<':
                depth = 0
                while True:
                    if text[at] == '<':
                        depth += 1
                    elif text[at] == '>':
                        depth -= 1
                        if not depth:
                            at += 1
                            break
                    at += 1
        elif at < len(text) and text[at] == '"':
            at = text.index('"', at + 1) + 1
    elif ch == '^':
        _, at = parse_type(text, at + 1)
    elif ch in '{(':
        depth = 0
        while True:
            if text[at] in '{(':
                depth += 1
            elif text[at] in '})':
                depth -= 1
                if not depth:
                    at += 1
                    break
            elif text[at] == '"':
                at = text.index('"', at + 1)
            at += 1
    elif ch == '[':
        at += 1
        while text[at].isdigit():
            at += 1
        _, at = parse_type(text, at)
        at += 1
    elif ch == 'b':
        at += 1
        while at < len(text) and text[at].isdigit():
            at += 1
    else:
        at += 1
    return text[start:at], at


def method_arguments(encoding):
    """(return type, [argument types]) of a method encoding, without self and _cmd."""
    types, at = [], 0
    while at < len(encoding):
        t, at = parse_type(encoding, at)
        while at < len(encoding) and encoding[at].isdigit():
            at += 1
        types.append(t)
    if len(types) < 3 or types[1] != '@' or types[2] != ':':
        fail('not a method encoding: ' + encoding)
    return types[0], types[3:]


def bitfield_struct(encoding):
    """A struct of bit-fields, `{Name=b1b1b1b29}`: (name, widths), or None."""
    m = BITFIELDS.match(encoding)
    return (m.group(1), [int(w) for w in re.findall(r'\d+', m.group(2))]) if m else None


def ctype_of(encoding):
    if encoding in CTYPES:
        return CTYPES[encoding]
    bits = bitfield_struct(encoding)
    return f'struct {bits[0]}' if bits and sum(bits[1]) <= 64 else None


def signature(types):
    ret, arguments = method_arguments(types)
    ctypes = [ctype_of(t) for t in [ret] + arguments]
    if any(c is None for c in ctypes):
        return None
    return ctypes[0], ctypes[1:]


def literal(ctype, value):
    if ctype.startswith('struct '):
        if isinstance(value, bool) or not isinstance(value, int):
            fail(f'profile value {value!r} of {ctype} is not the integer of its bits')
        return f'WMBits<{ctype}>({value}ull)'
    if ctype == 'BOOL':
        if not isinstance(value, bool):
            fail(f'profile value {value!r} is not a boolean')
        return 'YES' if value else 'NO'
    if ctype in ('float', 'double'):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            fail(f'profile value {value!r} is not a number')
        text = repr(float(value))
        return text + ('f' if ctype == 'float' else '')
    if ctype == 'MTLSize':
        if not (isinstance(value, list) and len(value) == 3):
            fail(f'profile value {value!r} is not a size')
        return 'MTLSizeMake(%s)' % ', '.join(f'{int(v)}ull' for v in value)
    if isinstance(value, bool) or not isinstance(value, int):
        fail(f'profile value {value!r} is not an integer')
    if ctype in ('NSUInteger', 'uint64_t'):
        return f'{value}ull'
    if ctype in ('NSInteger', 'int64_t'):
        return f'{value}ll'
    return f'{value}u' if ctype.startswith('uint') else f'{value}'


def selector_parts(selector, names):
    """`a:b:` with parameter names -> `a:(T)x b:(T)y`."""
    pieces = selector.split(':')[:-1]
    return ' '.join(f'{piece}:({ctype}){name}' for piece, (ctype, name) in zip(pieces, names))


def profile_comments(text):
    """{(section, key): comment} for every `key = value  # comment` line of the profile, and {(section, None): the
    comment lines above the section's header}."""
    out, section, block = {}, None, []
    for line in text.splitlines():
        if line.startswith('# '):
            block.append(line[2:])
            continue
        header = re.match(r'^\[(.+?)\]', line)
        if header:
            section = header.group(1)
            out[(section, None)] = ' '.join(block)
            block = []
            continue
        block = []
        entry = re.match(r'^("[^"]+"|[^ =]+) = .*?  # (.*)$', line)
        if entry and section:
            out[(section, entry.group(1).strip('"'))] = entry.group(2)
    return out


def device_rows(surface):
    """{selector: (types, carrying class)} of the instance driver selectors the device's classes carry."""
    rows = {}
    for cls in DEVICE_CHAIN:
        m = re.search(r'inline constexpr DriverSelector k%sDriver\[\] = \{\n(.*?)\n\};' % cls, surface, re.S)
        if not m:
            fail(f'{SURFACE.relative_to(ROOT)} has no driver selectors of {cls}')
        for kind, name, types, _definer, _detail in ROW.findall(m.group(1)):
            if kind == 'Instance':
                rows[name] = (types, cls)
    return rows


class ProfileGetters:
    """Methods answering driver selectors from the device profile."""

    def __init__(self, profile_text, rows):
        self.profile = tomllib.loads(profile_text)
        self.comments = profile_comments(profile_text)
        self.rows = rows
        self.methods = {}  # selector -> (source, provenance)
        for section in ('getters', 'driver_getters'):
            for selector, value in sorted(self.profile.get(section, {}).items()):
                if selector in rows:
                    self.getter(section, selector, value)
        for selector, table in sorted(self.profile.get('queries', {}).items()):
            if selector in rows:
                self.query(selector, table)
        self.heap()

    def getter(self, section, selector, value):
        types = self.rows[selector][0]
        sig = signature(types)
        if not sig or sig[1]:
            fail(f'{section}.{selector}: encoding {types} is not a zero-argument scalar getter')
        ctype = sig[0]
        provenance = self.comments[(section, selector)]
        self.methods[selector] = (f'- ({ctype}){selector}\n{{\n    return {literal(ctype, value)};\n}}\n', provenance)

    def query(self, selector, table):
        """An integer-argument query evaluated over a finite domain; other arguments are LOUD."""
        types = self.rows[selector][0]
        sig = signature(types)
        if not sig or len(sig[1]) != 1 or sig[1][0] not in ('NSUInteger', 'NSInteger', 'uint32_t', 'int32_t'):
            fail(f'queries.{selector}: encoding {types} is not a one-integer-argument query')
        ctype, (argtype,) = sig
        if self.profile.get('query_failures', {}).get(selector):
            # Metal.framework reports validation failures for some arguments: the plugin implements the query by hand
            # through the same framework path, or it stays LOUD.
            return
        domain = sorted(map(int, table))
        if domain != list(range(domain[0], domain[-1] + 1)):
            fail(f'queries.{selector}: the profiled domain is not contiguous')
        lines = [f'- ({ctype}){selector_parts(selector, [(argtype, "argument")])}', '{', '    switch (argument) {']
        for key in sorted(table, key=int):
            comment = self.comments.get((f'queries."{selector}"', key), '').split(';')[0]
            lines.append(f'    case {int(key)}: return {literal(ctype, table[key])};  // {comment}'.rstrip())
        lines += ['    }',
                  '    WMLoudArgument(self, _cmd, (uint64_t)argument,',
                  f'                   "the device profile evaluates this query only for the arguments {domain[0]} to '
                  f'{domain[-1]}");', '}', '']
        section = 'queries."%s"' % selector
        self.methods[selector] = ('\n'.join(lines),
                                  f'{self.comments[(section, None)]} Arguments {domain[0]} to {domain[-1]}.')

    def heap(self):
        selector = 'heapBufferSizeAndAlignWithLength:options:'
        heap = self.profile.get('heap', {})
        if selector not in self.rows or not heap:
            return
        if set(heap) != {'buffer_size_is_length', 'buffer_alignment'} or heap['buffer_size_is_length'] is not True:
            fail(f'[heap] does not state a length-preserving size with one alignment: {heap}')
        types = self.rows[selector][0]
        if signature(types) != ('MTLSizeAndAlign', ['NSUInteger', 'NSUInteger']):
            fail(f'{selector}: unexpected encoding {types}')
        provenance = self.comments[('heap', None)] + ' ' + self.comments[('heap', 'buffer_size_is_length')]
        body = ('- (MTLSizeAndAlign)heapBufferSizeAndAlignWithLength:(NSUInteger)length '
                'options:(MTLResourceOptions)options\n'
                f'{{\n    return MTLSizeAndAlign{{length, {literal("NSUInteger", heap["buffer_alignment"])}}};\n}}\n')
        self.methods[selector] = (body, provenance)


def source(getters):
    out = ['// Generated by guest/gen/profilegetters.py from the device profile the build was given. Do not edit.',
           '// Capability getters of the A10X: the driver selectors whose answer the device profile records, on the',
           '// device class that carries each. The comment above each method is the profile\'s provenance.', '',
           '#import "../Plugin/WebMetalDevice.h"', '#import "../Plugin/WebMetalReport.h"', '',
           '// The classes adopt the protocols that declare these methods; these categories, not the classes, implement',
           '// them.',
           '#pragma clang diagnostic ignored "-Wobjc-protocol-method-implementation"', '']
    structs = {}
    for selector in sorted(getters.methods):
        ret, arguments = method_arguments(getters.rows[selector][0])
        for encoding in [ret] + arguments:
            bits = bitfield_struct(encoding)
            if bits:
                structs[bits[0]] = bits[1]
    if structs:
        out += ['#include <cstring>', '', '// Bit-field structures of the 21H461 method encodings.']
        for name, widths in sorted(structs.items()):
            word = 'uint32_t' if sum(widths) <= 32 else 'uint64_t'
            fields = ' '.join(f'{word} f{i} : {w};' for i, w in enumerate(widths))
            out.append(f'struct {name} {{ {fields} }};')
        out += ['', '// The structure whose bits, read as a little-endian integer, are `bits`, as the profile records it.',
                'template <typename T> static T WMBits(unsigned long long bits)', '{',
                '    T value;', '    static_assert(sizeof value <= sizeof bits, "bit-field structure wider than its value");',
                '    memcpy(&value, &bits, sizeof value);', '    return value;', '}', '']
    by_class = {}
    for selector in sorted(getters.methods):
        by_class.setdefault(getters.rows[selector][1], []).append(selector)
    for cls in DEVICE_CHAIN:
        if cls not in by_class:
            continue
        out += [f'@implementation {cls} (WebMetalProfile)', '']
        for selector in by_class[cls]:
            body, provenance = getters.methods[selector]
            out += ['// ' + line for line in textwrap.wrap(provenance, 117)]
            out.append(body)
        out += ['@end', '']
    return '\n'.join(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--profile', default=os.environ.get('WEBMETAL_PROFILE'),
                        help='the device profile (default: $WEBMETAL_PROFILE)')
    parser.add_argument('--out', type=Path, default=OUTPUT)
    args = parser.parse_args()
    if not args.profile:
        fail('no device profile: pass --profile or set WEBMETAL_PROFILE (host/src/format/profile.rs describes it)')
    if not SURFACE.exists():
        fail(f'{SURFACE.relative_to(ROOT)} is missing: it is made from the user\'s own 21H461 root '
             '(guest/Plugin/WebMetalSurface.h)')
    try:
        profile_text = Path(args.profile).read_text()
    except OSError as e:
        fail(f'the device profile {args.profile} cannot be read: {e}')
    getters = ProfileGetters(profile_text, device_rows(SURFACE.read_text()))
    if not getters.methods:
        fail('the device profile answers none of the device classes\' driver selectors')
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(source(getters))
    print(f'{args.out}: {len(getters.methods)} methods')


if __name__ == '__main__':
    sys.exit(main())
