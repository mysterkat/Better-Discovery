// Hide the console window on Windows release builds; keep it in debug so we
// can watch the sidecar logs.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod ipc;
mod paths;
mod sidecar;
mod windows;

use tauri::{Manager, RunEvent};

use crate::sidecar::AppState;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_updater::Builder::new().build())
        .plugin(tauri_plugin_process::init())
        .manage(AppState::default())
        .invoke_handler(tauri::generate_handler![ipc::get_backend_port])
        .setup(|app| {
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                if let Err(e) = sidecar::spawn(handle) {
                    eprintln!("[sidecar] failed: {e}");
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri build")
        .run(|app, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                if let Some(state) = app.try_state::<AppState>() {
                    sidecar::shutdown(&state);
                }
            }
        });
}
