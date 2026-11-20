//! The A10X device profile's records the format module reads.
//!
//! The profile is a TOML file the user supplies at build time: `WEBMETAL_PROFILE` names its path, and the build fails
//! without it (build.rs). It describes the device's Metal driver as the user's own device answers; the repository
//! holds none of its values. Comments in it are ignored. The build reads:
//!
//! - `[pixel_format_list]`: `known`, the `MTLPixelFormat` values `MTLPixelFormatGetInfoForDevice` knows for the
//!   device, strictly increasing; `private`, those of them that are not in the SDK's `MTLPixelFormat` enum.
//! - `[pixel_formats.N]`, one table per known value `N`: `word_08`, the 32-bit flag word at byte offset 8 of the
//!   `MTLPixelFormatInfo` the function fills, and the booleans that decode its bits, each checked against the word:
//!   `supported` (bit 0), `compressed` (bit 10), `compatible_float` (16), `compatible_half` (17), `compatible_int`
//!   (18), `compatible_uint` (19), `compatible_short` (20), `compatible_ushort` (21), `compatible_ulong` (29).
//! - `[getters]` (`MTLDevice` and `MTLDeviceSPI` getters) and `[driver_getters]` (driver-class methods outside the
//!   protocols): a boolean for each selector of [`A10xGetters`], in either table.
//!
//! The tests also read, from the same file:
//!
//! - `[heap]`: `buffer_size_is_length` (boolean) and `buffer_alignment` (integer), what
//!   `heapBufferSizeAndAlignWithLength:options:` answers.
//! - `[queries."minimumLinearTextureAlignmentForPixelFormat:"]` and
//!   `[queries."minimumTextureBufferAlignmentForPixelFormat:"]`: `N = alignment` for each format `N` the query
//!   answers.
//!
//! A value missing, of the wrong type, or disagreeing with its flag word stops the build.

/// A pixel format record of the profile (`[pixel_formats.N]`): the formats `MTLPixelFormatGetInfoForDevice` knows for
/// the device, with the flag word at offset 8 of `MTLPixelFormatInfo`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct A10xFormatRecord {
    pub value: u32,
    pub word_08: u32,
    /// Bit 0 of `word_08`.
    pub supported: bool,
    /// Bit 10 of `word_08`.
    pub compressed: bool,
    /// Not in the SDK's `MTLPixelFormat` enum (`pixel_format_list.private`).
    pub private: bool,
}

/// The capability getters the format module consults (`[getters]` or `[driver_getters]` of the profile), each named
/// after its selector.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct A10xGetters {
    /// `isASTCPixelFormatsSupported`
    pub is_astc_pixel_formats_supported: bool,
    /// `supportsASTCTextureCompression`
    pub supports_astc_texture_compression: bool,
    /// `supportsASTCHDRTextureCompression`
    pub supports_astc_hdr_texture_compression: bool,
    /// `supports3DASTCTextures`
    pub supports_3d_astc_textures: bool,
    /// `supportsBCTextureCompression`
    pub supports_bc_texture_compression: bool,
    /// `isBCTextureCompressionSupported`
    pub is_bc_texture_compression_supported: bool,
    /// `supports3DBCTextures`
    pub supports_3d_bc_textures: bool,
    /// `supportsCompressedTextureViewSPI`
    pub supports_compressed_texture_view_spi: bool,
    /// `isFloat32FilteringSupported`
    pub is_float32_filtering_supported: bool,
    /// `isRGB10A2GammaSupported`
    pub is_rgb10a2_gamma_supported: bool,
    /// `supportsExtendedXR10Formats`
    pub supports_extended_xr10_formats: bool,
    /// `supportsPublicXR10Formats`
    pub supports_public_xr10_formats: bool,
    /// `supportsExtendedYUVFormats`
    pub supports_extended_yuv_formats: bool,
    /// `supportsYCBCRFormats`
    pub supports_ycbcr_formats: bool,
    /// `supportsYCBCRFormats12`
    pub supports_ycbcr_formats_12: bool,
    /// `supportsYCBCRFormatsPQ`
    pub supports_ycbcr_formats_pq: bool,
    /// `supportsYCBCRFormatsXR`
    pub supports_ycbcr_formats_xr: bool,
    /// `supportsYCBCRPackedFormats12`
    pub supports_ycbcr_packed_formats_12: bool,
    /// `supportsYCBCRPackedFormatsPQ`
    pub supports_ycbcr_packed_formats_pq: bool,
    /// `supportsYCBCRPackedFormatsXR`
    pub supports_ycbcr_packed_formats_xr: bool,
}

// A10X_FORMATS (the records of `pixel_format_list.known`, in its order) and A10X_GETTERS, from the supplied profile.
include!(concat!(env!("OUT_DIR"), "/a10x_profile.rs"));

/// The text of the supplied profile, for the tests that compare the executor's constants with its other sections.
#[cfg(test)]
pub fn text() -> String {
    let path = env!("WEBMETAL_PROFILE_FILE");
    std::fs::read_to_string(path).unwrap_or_else(|e| panic!("the device profile {path} (WEBMETAL_PROFILE): {e}"))
}
