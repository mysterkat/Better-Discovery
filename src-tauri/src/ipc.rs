//! Tauri command handlers invoked from the frontend.
//!
//! Phase 4: just `get_backend_port`. Phase 5+ will add `open_results_window`,
//! `pick_file`, `pick_dir`.

use tauri::State;

use crate::sidecar::AppState;

#[tauri::command]
pub fn get_backend_port(state: State<'_, AppState>) -> Option<u16> {
    *state.backend_port.lock().unwrap()
}
