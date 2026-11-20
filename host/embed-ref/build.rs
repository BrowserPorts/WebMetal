//! With the `native` feature on wgpu-native, which `webmetal-host` links dynamically, the programs and tests of this
//! crate find the library where `webmetal-host`'s own targets do: `WEBGPU_NATIVE_DIR/lib`, added to their run-time
//! search path (`host/build.rs` describes the variables). A build script's link arguments apply only to its own
//! package's targets, so this one repeats them for this package's.
//!
//! `WEBMETAL_REFERENCE`, a run of the reference harness the user recorded on real Metal (`host/build.rs` describes
//! it), builds the tests that compare with it to run (`cfg(webmetal_reference)`); without it they are ignored, naming
//! the variable. A path given but missing stops the build.

fn main() {
    println!("cargo:rerun-if-changed=build.rs");
    reference();
    println!("cargo:rerun-if-env-changed=WEBGPU_NATIVE");
    println!("cargo:rerun-if-env-changed=WEBGPU_NATIVE_DIR");
    if std::env::var_os("CARGO_FEATURE_NATIVE").is_none() {
        return;
    }
    if std::env::var("WEBGPU_NATIVE").as_deref() == Ok("wgpu-native")
        && let Ok(root) = std::env::var("WEBGPU_NATIVE_DIR")
    {
        let lib = std::path::Path::new(&root).join("lib");
        println!("cargo:rustc-link-arg=-Wl,-rpath,{}", lib.display());
    }
}

fn reference() {
    println!("cargo:rustc-check-cfg=cfg(webmetal_reference)");
    println!("cargo:rerun-if-env-changed=WEBMETAL_REFERENCE");
    let path = match std::env::var("WEBMETAL_REFERENCE") {
        Ok(v) if !v.is_empty() => v,
        _ => return,
    };
    let dir =
        std::fs::canonicalize(&path).unwrap_or_else(|e| panic!("{path} (WEBMETAL_REFERENCE) cannot be opened: {e}"));
    if !dir.join("release").is_dir() {
        panic!("{path} (WEBMETAL_REFERENCE) has no release");
    }
    println!("cargo:rerun-if-changed={}", dir.display());
    println!("cargo:rustc-cfg=webmetal_reference");
    println!("cargo:rustc-env=WEBMETAL_REFERENCE_DIR={}", dir.display());
}
