//! The A10X's texture memory layout: where the A10X Metal driver (AGXMetalA10 of iPadOS 17.7.11, 21H461) puts each
//! texel of a texture whose memory the CPU sees, and the driver's CPU copies between that memory and linear bytes.
//!
//! A texture made over caller memory (`newTextureWithBytesNoCopy:length:descriptor:deallocator:`) and the memory a
//! texture layout object fills (`-[MTLTextureLayout copyFromLinearBytes:...]`, which QuartzCore uses to prepare it)
//! hold texels in this layout, not Metal's linear one. The executor reads and writes such memory with this module.
//!
//! - [`gen2`]: the texture object the driver builds from a descriptor (`AGX::TextureGen2`): kind (linear or
//!   twiddled), level offsets, slice sizes, twiddled block information and the layout's size and alignment.
//! - [`region`]: `writeRegion` and `readRegion`, with `validateRegion`'s refusals.
//! - [`guest`]: the copies of a texture layout object, between two blocks of a task's memory.
//! - [`formats`]: the fields of the driver's format table the layout reads.
//!
//! The format table is not part of the repository: it is read from the user's own copy of the driver, and the build
//! includes the Rust file `WEBMETAL_AGX_TEXTURE_FORMATS` names (build.rs). The file defines, against
//! [`gen2::FormatInfo`] (imported with `use super::gen2::FormatInfo;`):
//!
//! - `CHOOSE_TEXTURE_FORMAT_LIMIT: u32`: the largest `pixelFormat - 1` that
//!   `AGX::G9::TextureFormatTable::chooseTextureFormatOBJC` looks up in its table;
//! - `UNSUPPORTED_FORMAT: FormatInfo`: the fields of `_texFormatUnsupported`, the entry of every other value (its
//!   `pixel_format` is 0);
//! - `FORMATS: [FormatInfo; N]`: the fields of every other entry the function chooses, sorted by `pixel_format`;
//! - `CLASS_WORD_MASK: u32` and `CLASS_WORDS: [u32; 17]`: the words `TextureGen2` reads by entry class - 2, and the
//!   classes whose word it reads;
//! - `COLOR_SPACE_CONVERSIONS: [u32; 10]`: the driver's color-space conversion for `MTLTextureDescriptor`
//!   `colorSpaceConversion` 1 to 10.

/// The driver's format table, from the file the build is given.
pub mod formats {
    include!(concat!(env!("OUT_DIR"), "/agx_texture_formats.rs"));
}
pub mod gen2;
pub mod guest;
pub mod region;
