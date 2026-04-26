use std::fs;
use std::io;
use std::path::Path;

fn main() {
    let backend_dir = Path::new("..").join("backend");
    if let Err(err) = prune_python_build_artifacts(&backend_dir) {
        println!(
            "cargo:warning=Could not fully prune Python cache files under {}: {}",
            backend_dir.display(),
            err
        );
    }

    // The default 1 MB Windows stack overflows when Tauri enumerates thousands
    // of Python runtime files from the resource glob. Run in a thread with a
    // 64 MB stack to give the build script enough room.
    std::thread::Builder::new()
        .stack_size(64 * 1024 * 1024)
        .spawn(|| tauri_build::build())
        .expect("failed to spawn build thread")
        .join()
        .expect("build thread panicked");
}

fn prune_python_build_artifacts(dir: &Path) -> io::Result<()> {
    if !dir.is_dir() {
        return Ok(());
    }

    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();
        let file_type = entry.file_type()?;

        if file_type.is_dir() {
            if entry.file_name() == "__pycache__" {
                let _ = fs::remove_dir_all(&path);
                continue;
            }
            prune_python_build_artifacts(&path)?;
            continue;
        }

        if path.extension().is_some_and(|ext| ext.eq_ignore_ascii_case("pyc")) {
            let _ = fs::remove_file(&path);
        }
    }

    Ok(())
}
