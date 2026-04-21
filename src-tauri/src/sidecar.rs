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
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};

use crate::paths::{app_root, resolve_backend_dir, resolve_python_exe};

#[derive(Default)]
pub struct AppState {
    pub backend_port: Mutex<Option<u16>>,
    pub child: Mutex<Option<std::process::Child>>,
}

#[derive(Serialize, Clone)]
pub struct BackendReady {
    pub port: u16,
}

pub fn spawn(app: AppHandle) -> Result<(), String> {
    // Tauri resource directory — Some in production (installer), may error in dev.
    let resource_dir = app.path().resource_dir().ok();

    let root = app_root().ok_or("could not resolve app root")?;

    let backend_dir =
        resolve_backend_dir(&root, resource_dir.as_deref())
            .ok_or_else(|| format!("backend dir not found (root={})", root.display()))?;

    let python = resolve_python_exe(&root, resource_dir.as_deref());
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

    let mut child = cmd
        .args([
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
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| format!("spawn uvicorn: {e}"))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();

    if let Some(state) = app.try_state::<AppState>() {
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
    let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| format!("bind: {e}"))?;
    let port = listener
        .local_addr()
        .map_err(|e| format!("local_addr: {e}"))?
        .port();
    drop(listener);
    Ok(port)
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

/// Kill the sidecar if it's still running. Called on window/app close.
pub fn shutdown(state: &AppState) {
    if let Some(mut child) = state.child.lock().unwrap().take() {
        eprintln!("[sidecar] shutting down (pid {})", child.id());
        let _ = child.kill();
        let _ = child.wait();
    }
}
