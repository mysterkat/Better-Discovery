//! Spawn the FastAPI backend as a child process and surface readiness.
//!
//! - Picks a free 127.0.0.1 port via TcpListener(:0), drops it, passes to uvicorn.
//! - Pipes child stdout/stderr into our stderr with a `[sidecar ...]` prefix.
//! - Watches output for the "Uvicorn running on" or "Application startup complete"
//!   marker; on match emits the `backend-ready` Tauri event with the port.
//! - Stores the Child handle in AppState so we can kill it on shutdown.
//!
//! Phase 8: uses `app.path().resource_dir()` to support production bundles where
//! the backend and embedded Python live inside the Tauri resource directory.

use std::io::{BufRead, BufReader, Read};
use std::net::TcpListener;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::paths::{app_root, resolve_backend_dir, resolve_python_exe};

#[derive(Default)]
pub struct AppState {
    pub backend_port: Mutex<Option<u16>>,
    pub child: Mutex<Option<std::process::Child>>,
    pub shutting_down: AtomicBool,
}

#[derive(Serialize, Clone)]
pub struct BackendReady {
    pub port: u16,
}

pub fn spawn(app: AppHandle) -> Result<(), String> {
    spawn_child(&app)?;

    let monitor_app = app.clone();
    std::thread::spawn(move || monitor(monitor_app));

    Ok(())
}

fn spawn_child(app: &AppHandle) -> Result<(), String> {
    // Tauri resource directory - Some in production (installer), may error in dev.
    let resource_dir = app.path().resource_dir().ok();

    let root = app_root().ok_or("could not resolve app root")?;

    let backend_dir = resolve_backend_dir(&root, resource_dir.as_deref())
        .ok_or_else(|| format!("backend dir not found (root={})", root.display()))?;

    let python = resolve_python_exe(&root, resource_dir.as_deref());

    // v0.6.0: refresh embedded-Python deps when backend/requirements.txt
    // has changed since the last successful install. Tracks a fingerprint
    // file next to the Python interpreter — pure-stdlib (no sha crate).
    // Silently best-effort: pip failure (offline, etc.) doesn't block boot.
    if let Err(e) = ensure_deps_synced(&python, &backend_dir) {
        eprintln!("[sidecar] dep refresh skipped: {e}");
    }

    let port = reserve_port()?;

    eprintln!(
        "[sidecar] starting: python={} backend={} port={}",
        python.display(),
        backend_dir.display(),
        port
    );

    // Build environment: make sure embedded Python can find its own site-packages
    // when the runtime was installed by setup_embedded_python.ps1.
    let mut cmd = Command::new(&python);
    cmd.current_dir(&backend_dir)
        .env("BD_BACKEND_PORT", port.to_string())
        .env("PYTHONUNBUFFERED", "1");

    // If the python exe lives inside our embedded runtime, add its
    // site-packages to PYTHONPATH so uvicorn finds all installed wheels.
    if let Some(py_dir) = python.parent() {
        let site_pkgs = py_dir.join("Lib").join("site-packages");
        if site_pkgs.is_dir() {
            let existing = std::env::var("PYTHONPATH").unwrap_or_default();
            let sep = if existing.is_empty() { "" } else { ";" };
            cmd.env(
                "PYTHONPATH",
                format!("{}{sep}{}", site_pkgs.display(), existing),
            );
        }
    }

    cmd.args([
        "-u",
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
        "--log-level",
        "info",
    ])
    .stdout(Stdio::piped())
    .stderr(Stdio::piped());

    // Hide the Python sidecar's console window on Windows. Without this the
    // child process spawns its own visible cmd window (the parent app already
    // sets windows_subsystem = "windows" in main.rs, so it has no console of
    // its own). Captured stdout/stderr still flow through the watch threads.
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("spawn uvicorn: {e}"))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    if let Some(state) = app.try_state::<AppState>() {
        *state.backend_port.lock().unwrap() = None;
        *state.child.lock().unwrap() = Some(child);
    }

    let app_stdout = app.clone();
    if let Some(s) = stdout {
        std::thread::spawn(move || watch(s, "stdout", port, app_stdout));
    }
    let app_stderr = app.clone();
    if let Some(s) = stderr {
        std::thread::spawn(move || watch(s, "stderr", port, app_stderr));
    }

    Ok(())
}

fn reserve_port() -> Result<u16, String> {
    // Avoid the OS-assigned ephemeral range (49152-65535 on Windows). Ports in
    // that pool can be taken by outbound TCP connections between the moment we
    // release the listener and the moment uvicorn binds, producing
    // `[WinError 10013] permission denied` on bind. Pick from the IANA
    // registered range and verify we can bind, drop, and immediately rebind.
    use std::time::{SystemTime, UNIX_EPOCH};

    let mut seed = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos() as u64)
        .unwrap_or(0xC0FFEE_u64)
        | 1;

    let mut last_err = String::new();
    for _ in 0..50 {
        // xorshift step
        seed ^= seed << 13;
        seed ^= seed >> 7;
        seed ^= seed << 17;
        let port: u16 = 10_000 + (seed % 39_000) as u16; // 10000..=48999

        match TcpListener::bind(("127.0.0.1", port)) {
            Ok(l1) => {
                drop(l1);
                // Confirm we can rebind — if not, the port is in a Windows
                // exclusion range or was taken by another process.
                match TcpListener::bind(("127.0.0.1", port)) {
                    Ok(l2) => {
                        drop(l2);
                        return Ok(port);
                    }
                    Err(e) => last_err = format!("rebind {port}: {e}"),
                }
            }
            Err(e) => last_err = format!("bind {port}: {e}"),
        }
    }
    Err(format!(
        "could not find a usable backend port after 50 attempts ({last_err})"
    ))
}

fn watch<R: Read + Send + 'static>(stream: R, tag: &'static str, port: u16, app: AppHandle) {
    let reader = BufReader::new(stream);
    let mut emitted = false;
    for line in reader.lines().flatten() {
        eprintln!("[sidecar {tag}] {line}");
        if !emitted
            && (line.contains("Uvicorn running on")
                || line.contains("Application startup complete"))
        {
            if let Some(state) = app.try_state::<AppState>() {
                *state.backend_port.lock().unwrap() = Some(port);
            }
            let _ = app.emit("backend-ready", BackendReady { port });
            eprintln!("[sidecar] started (port {port})");
            emitted = true;
        }
    }
}

fn monitor(app: AppHandle) {
    loop {
        std::thread::sleep(Duration::from_secs(1));

        let Some(state) = app.try_state::<AppState>() else {
            return;
        };
        if state.shutting_down.load(Ordering::SeqCst) {
            return;
        }

        let exited = {
            let mut guard = state.child.lock().unwrap();
            match guard.as_mut() {
                Some(child) => match child.try_wait() {
                    Ok(Some(status)) => {
                        eprintln!("[sidecar] backend exited with status {status}");
                        *guard = None;
                        true
                    }
                    Ok(None) => false,
                    Err(err) => {
                        eprintln!("[sidecar] backend try_wait failed: {err}");
                        *guard = None;
                        true
                    }
                },
                None => true,
            }
        };

        if !exited {
            continue;
        }

        *state.backend_port.lock().unwrap() = None;

        if state.shutting_down.load(Ordering::SeqCst) {
            return;
        }

        eprintln!("[sidecar] attempting backend restart");
        if let Err(err) = spawn_child(&app) {
            eprintln!("[sidecar] restart failed: {err}");
        }
    }
}

/// v0.6.0: Detect requirements.txt change and re-sync embedded Python deps.
///
/// Uses (file len, mtime-secs) as a cheap fingerprint stored in
/// ``<python_dir>/.requirements_fingerprint``. When the fingerprint mismatches
/// (or is absent) we run ``python -m pip install -U -r requirements.txt`` and
/// write the new fingerprint. Failure is non-fatal — pip may not have
/// internet, and the rest of the app boots fine; consumers of optional deps
/// (yfinance for ^GSPC benchmark, etc.) already fall back gracefully.
fn ensure_deps_synced(python: &std::path::Path, backend_dir: &std::path::Path) -> Result<(), String> {
    use std::time::UNIX_EPOCH;

    let req_file = backend_dir.join("requirements.txt");
    if !req_file.is_file() {
        return Ok(()); // nothing to sync
    }
    let meta = std::fs::metadata(&req_file)
        .map_err(|e| format!("stat requirements.txt: {e}"))?;
    let len  = meta.len();
    let mtime = meta
        .modified()
        .ok()
        .and_then(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let fingerprint = format!("{len}-{mtime}");

    let py_dir = python
        .parent()
        .ok_or_else(|| "python has no parent dir".to_string())?;
    let sentinel = py_dir.join(".requirements_fingerprint");

    let cached = std::fs::read_to_string(&sentinel).ok().unwrap_or_default();
    if cached.trim() == fingerprint {
        return Ok(()); // up to date
    }

    eprintln!(
        "[sidecar] requirements.txt changed (cached={} new={}); syncing deps...",
        if cached.is_empty() { "<none>" } else { cached.trim() },
        fingerprint,
    );

    let mut cmd = Command::new(python);
    cmd.current_dir(backend_dir)
        .args(["-m", "pip", "install", "--disable-pip-version-check",
               "--upgrade", "-r", "requirements.txt"])
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    #[cfg(target_os = "windows")]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let status = cmd.status().map_err(|e| format!("spawn pip: {e}"))?;
    if !status.success() {
        return Err(format!("pip install exited with {status}"));
    }

    // Only update the sentinel on success — if pip failed, we'll retry next
    // boot. Cheap.
    if let Err(e) = std::fs::write(&sentinel, &fingerprint) {
        eprintln!("[sidecar] could not write fingerprint sentinel: {e}");
    }
    eprintln!("[sidecar] deps synced");
    Ok(())
}


/// Kill the sidecar if it's still running. Called on window/app close.
pub fn shutdown(state: &AppState) {
    state.shutting_down.store(true, Ordering::SeqCst);
    if let Some(mut child) = state.child.lock().unwrap().take() {
        eprintln!("[sidecar] shutting down (pid {})", child.id());
        let _ = child.kill();
        let _ = child.wait();
    }
}
