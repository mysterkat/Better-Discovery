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

/// Kill the sidecar if it's still running. Called on window/app close.
pub fn shutdown(state: &AppState) {
    state.shutting_down.store(true, Ordering::SeqCst);
    if let Some(mut child) = state.child.lock().unwrap().take() {
        eprintln!("[sidecar] shutting down (pid {})", child.id());
        let _ = child.kill();
        let _ = child.wait();
    }
}
