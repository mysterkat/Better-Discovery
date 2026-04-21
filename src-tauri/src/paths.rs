//! Resolve the app root and the Python interpreter.
//!
//! Resolution order — app root:
//!   1. BD_APP_ROOT env var
//!   2. Walk up from current_exe looking for a directory that contains backend/
//!      (production: <install_dir>/ after resources are placed there)
//!      (dev: BETTER DISCOVERY/ which also has src-tauri/)
//!   3. CARGO_MANIFEST_DIR/.. (dev-only fallback baked in at compile time)
//!
//! Resolution order — Python exe:
//!   1. BD_PYTHON_EXE env var
//!   2. <resource_dir>/python/python.exe  (production bundle)
//!   3. <root>/src-tauri/binaries/python/python.exe  (dev, after setup script)
//!   4. <root>/.venv/Scripts/python.exe              (dev venv)
//!   5. "python" on PATH
//!
//! Resolution order — backend directory:
//!   1. <resource_dir>/backend/   (production bundle)
//!   2. <root>/backend/           (dev / portable)

use std::path::{Path, PathBuf};

/// Returns the project / install root directory.
pub fn app_root() -> Option<PathBuf> {
    if let Ok(v) = std::env::var("BD_APP_ROOT") {
        let p = PathBuf::from(v);
        if p.exists() {
            return Some(p);
        }
    }

    // Walk up from the executable.  In production the install dir will have
    // a `backend/` directory (from Tauri resource bundling).  In dev the
    // project root also has `src-tauri/` alongside `backend/`.
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(|p| p.to_path_buf());
        while let Some(dir) = cur {
            if dir.join("backend").is_dir() {
                return Some(dir);
            }
            cur = dir.parent().map(|p| p.to_path_buf());
        }
    }

    // CARGO_MANIFEST_DIR is src-tauri/; parent is the project root.
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.parent().map(|p| p.to_path_buf())
}

/// Resolve the Python executable given:
/// - `root`         – project / install root (from `app_root()`)
/// - `resource_dir` – Tauri resource directory (from `app.path().resource_dir()`)
pub fn resolve_python_exe(root: &Path, resource_dir: Option<&Path>) -> PathBuf {
    if let Ok(v) = std::env::var("BD_PYTHON_EXE") {
        let p = PathBuf::from(v);
        if p.exists() {
            return p;
        }
    }

    // Production: runtime installed to %LOCALAPPDATA%\BETTER DISCOVERY\python\
    // by setup_embedded_python.ps1 — not bundled inside the installer.
    if let Ok(local) = std::env::var("LOCALAPPDATA") {
        let p = PathBuf::from(local)
            .join("BETTER DISCOVERY")
            .join("python")
            .join("python.exe");
        if p.exists() {
            return p;
        }
    }

    // Fallback: embedded runtime bundled as a Tauri resource (legacy).
    if let Some(rd) = resource_dir {
        let p = rd.join("python").join("python.exe");
        if p.exists() {
            return p;
        }
    }

    // Dev: setup_embedded_python.ps1 has been run.
    let embedded = root
        .join("src-tauri")
        .join("binaries")
        .join("python")
        .join("python.exe");
    if embedded.exists() {
        return embedded;
    }

    // Dev: ordinary virtual-env.
    let dev_venv = root.join(".venv").join("Scripts").join("python.exe");
    if dev_venv.exists() {
        return dev_venv;
    }

    // Last resort: system PATH.
    PathBuf::from("python")
}

/// Resolve the backend source directory given:
/// - `root`         – project / install root
/// - `resource_dir` – Tauri resource directory
pub fn resolve_backend_dir(root: &Path, resource_dir: Option<&Path>) -> Option<PathBuf> {
    // Production: bundled into Tauri resource dir.
    if let Some(rd) = resource_dir {
        let p = rd.join("backend");
        if p.is_dir() {
            return Some(p);
        }
    }

    // Dev / portable: adjacent to project root.
    let p = root.join("backend");
    if p.is_dir() {
        return Some(p);
    }

    None
}
