fn main() {
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
