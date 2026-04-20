//! Resolve the app root (BETTER DISCOVERY/) and the Python interpreter.
//!
//! Order of precedence:
//!   app root:
//!     1. env BD_APP_ROOT
//!     2. walk up from current_exe looking for both `backend/` and `src-tauri/`
//!     3. CARGO_MANIFEST_DIR/.. (dev fallback)
//!
//!   python exe:
//!     1. env BD_PYTHON_EXE
//!     2. <root>/src-tauri/binaries/python/python.exe  (Phase 8 embedded)
//!     3. <root>/.venv/Scripts/python.exe              (dev venv)
//!     4. PATH `python`

use std::path::{Path, PathBuf};

pub fn app_root() -> Option<PathBuf> {
    if let Ok(v) = std::env::var("BD_APP_ROOT") {
        let p = PathBuf::from(v);
        if p.exists() {
            return Some(p);
        }
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(|p| p.to_path_buf());
        while let Some(dir) = cur {
            if dir.join("backend").is_dir() && dir.join("src-tauri").is_dir() {
                return Some(dir);
            }
            cur = dir.parent().map(|p| p.to_path_buf());
        }
    }
    // CARGO_MANIFEST_DIR is src-tauri/; parent is the project root.
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest.parent().map(|p| p.to_path_buf())
}

pub fn resolve_python_exe(root: &Path) -> PathBuf {
    if let Ok(v) = std::env::var("BD_PYTHON_EXE") {
        let p = PathBuf::from(v);
        if p.exists() {
            return p;
        }
    }
    let embedded = root.join("src-tauri").join("binaries").join("python").join("python.exe");
    if embedded.exists() {
        return embedded;
    }
    let dev_venv = root.join(".venv").join("Scripts").join("python.exe");
    if dev_venv.exists() {
        return dev_venv;
    }
    PathBuf::from("python")
}
