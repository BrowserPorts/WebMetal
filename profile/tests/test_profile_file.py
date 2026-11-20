"""Checks of a device profile that need neither the target root nor the host GPU.

The profile is the one the user extracted and names with WEBMETAL_PROFILE; without it the tests are
skipped, saying so. The checks of the A10X's own values run when the profile describes AGXA10XDevice.
"""

import os
import re
import tomllib
import unittest
from pathlib import Path

PROFILE = Path(os.environ['WEBMETAL_PROFILE']) if os.environ.get('WEBMETAL_PROFILE') else None
NOT_SUPPLIED = 'WEBMETAL_PROFILE is not set: the device profile is not supplied'
LINE = re.compile(r'^(?P<key>"[^"]+"|[A-Za-z0-9_-]+) = (?P<value>[^#]+?)  # (?P<comment>\S.*)$')


def describes(device_class):
    return PROFILE is not None and PROFILE.read_text().startswith(f'# Metal device profile of {device_class}:')


@unittest.skipUnless(PROFILE, NOT_SUPPLIED)
class ProfileFile(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = PROFILE.read_text()
        cls.data = tomllib.loads(cls.text)

    def test_values_are_numbers_and_booleans(self):
        def check(path, node):
            if isinstance(node, dict):
                for k, v in node.items():
                    check(f'{path}.{k}', v)
            elif isinstance(node, list):
                for v in node:
                    self.assertIsInstance(v, (bool, int, float), path)
            else:
                self.assertIsInstance(node, (bool, int, float), path)
        check('', self.data)

    def test_every_value_line_has_provenance(self):
        for number, line in enumerate(self.text.splitlines(), 1):
            if not line or line.startswith('#') or line.startswith('['):
                continue
            match = LINE.match(line)
            self.assertIsNotNone(match, f'line {number}: {line}')
            comment = match.group('comment')
            self.assertNotIn('disagree', comment.lower(), f'line {number}')

    def test_verdict_on_every_line_of_the_main_sections(self):
        section = None
        for number, line in enumerate(self.text.splitlines(), 1):
            if line.startswith('['):
                section = line.split(']')[0][1:]
                continue
            if not LINE.match(line or ''):
                continue
            if section in ('getters', 'driver_getters', 'limits') or section.startswith(('queries.', 'pixel_formats.')):
                self.assertRegex(line, r'# .*\bA\b', f'line {number}')
                self.assertRegex(line, r'\bB[12] ', f'line {number}')

    def test_sections(self):
        for name in ('getters', 'driver_getters', 'runtime', 'unimplemented', 'not_evaluated', 'limits',
                     'limits_runtime', 'kernel_inputs', 'initial_state', 'program_sdk', 'pixel_format_list',
                     'pixel_formats', 'sample_positions', 'sample_position_failures', 'heap', 'queries'):
            self.assertIn(name, self.data)

    @unittest.skipUnless(describes('AGXA10XDevice'), 'the profile does not describe the A10X')
    def test_limits_are_all_confirmed(self):
        limits = [line for line in self.text.split('\n[limits]\n')[1].split('\n\n')[0].splitlines()
                  if LINE.match(line)]
        # _MTLDevice._limits has 65 fields on 21H461; maxBufferLength is kernel-supplied.
        self.assertEqual(len(limits) + len(self.data['limits_runtime']), 65)
        for line in limits:
            self.assertIn('B1 agrees', line)
        self.assertEqual(self.data['limits_runtime'], {'maxBufferLength': True})

    @unittest.skipUnless(describes('AGXA10XDevice'), 'the profile does not describe the A10X')
    def test_getters_read_directly_from_the_driver(self):
        g = self.data['getters']
        self.assertTrue(g['supportsMemorylessRenderTargets'])
        self.assertEqual(g['maxFragmentTextures'], 31)
        self.assertEqual(g['maxIndirectTextures'], 31)
        self.assertEqual(g['maxThreadgroupMemoryLength'], 0x4000)
        for name in ('supportsTexture2DMultisampleArray', 'supportsBCTextureCompression', 'supportsMeshShaders',
                     'supportsDynamicLibraries', 'supportsVertexAmplification', 'supportsQueryTextureLOD',
                     'supportsBufferBoundsChecking'):
            self.assertFalse(g[name], name)
        self.assertFalse(self.data['driver_getters']['supportsAtomicFloat'])
        self.assertEqual(g['indirectArgumentBufferCapabilities'], 1)
        counts = self.data['queries']['supportsSampleCount:']
        self.assertEqual(sorted(int(k) for k, v in counts.items() if v), [1, 2, 4])

    @unittest.skipUnless(describes('AGXA10XDevice'), 'the profile does not describe the A10X')
    def test_families(self):
        families = self.data['queries']['supportsFamily:']
        supported = sorted(int(k) for k, v in families.items() if v)
        self.assertEqual(supported, [1001, 1002, 1003, 3001, 3002])

    @unittest.skipUnless(describes('AGXA10XDevice'), 'the profile does not describe the A10X')
    def test_default_sample_positions(self):
        positions = self.data['sample_positions']
        self.assertEqual(positions['count_4'], [0.375, 0.125, 0.875, 0.375, 0.125, 0.625, 0.625, 0.875])
        self.assertEqual(positions['count_2'], [0.75, 0.75, 0.25, 0.25])

    def test_pixel_format_records(self):
        formats = self.data['pixel_formats']
        known = self.data['pixel_format_list']['known']
        self.assertEqual(sorted(int(k) for k in formats), sorted(known))
        for fmt, record in formats.items():
            self.assertEqual(record['supported'], bool(record['word_08'] & 1), fmt)
            self.assertEqual(record['compressed'], bool(record['word_08'] >> 10 & 1), fmt)
        self.assertTrue(set(self.data['pixel_format_list']['private']) <= set(known))


if __name__ == '__main__':
    unittest.main()
