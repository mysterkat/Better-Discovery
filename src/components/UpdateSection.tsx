import { useEffect, useState } from "react";
import {
  checkForUpdate,
  downloadAndInstall,
  getCurrentVersion,
  type UpdateState,
} from "../lib/updater";

export default function UpdateSection() {
  const [currentVersion, setCurrentVersion] = useState<string>("…");
  const [state, setState] = useState<UpdateState>({ status: "idle" });

  useEffect(() => {
    getCurrentVersion().then(setCurrentVersion);
  }, []);

  const onCheck = async () => {
    setState({ status: "checking" });
    try {
      const update = await checkForUpdate();
      if (!update) {
        setState({ status: "up-to-date" });
        return;
      }
      setState({
        status: "available",
        version: update.version,
        notes: update.body ?? null,
      });
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  const onInstall = async () => {
    if (state.status !== "available") return;
    setState({ status: "downloading", downloaded: 0, total: null });
    try {
      const update = await checkForUpdate();
      if (!update) {
        setState({ status: "up-to-date" });
        return;
      }
      await downloadAndInstall(update, (downloaded, total) => {
        setState({ status: "downloading", downloaded, total });
      });
      setState({ status: "installing" });
      // App will relaunch — this component unmounts.
    } catch (err) {
      setState({
        status: "error",
        message: err instanceof Error ? err.message : String(err),
      });
    }
  };

  return (
    <section className="settings-section">
      <h3>Updates</h3>
      <p className="settings-meta">Current version: {currentVersion}</p>

      {state.status === "idle" && (
        <button className="settings-button" onClick={onCheck}>
          Check for updates
        </button>
      )}

      {state.status === "checking" && <p>Checking…</p>}

      {state.status === "up-to-date" && (
        <>
          <p>You're on the latest version.</p>
          <button className="settings-button" onClick={onCheck}>
            Check again
          </button>
        </>
      )}

      {state.status === "available" && (
        <>
          <p>
            Version <strong>{state.version}</strong> is available.
          </p>
          {state.notes && <pre className="settings-notes">{state.notes}</pre>}
          <button className="settings-button" onClick={onInstall}>
            Download and install
          </button>
        </>
      )}

      {state.status === "downloading" && (
        <p>
          Downloading…{" "}
          {state.total
            ? `${Math.round((state.downloaded / state.total) * 100)}%`
            : `${(state.downloaded / 1024 / 1024).toFixed(1)} MB`}
        </p>
      )}

      {state.status === "installing" && <p>Installing… app will restart.</p>}

      {state.status === "error" && (
        <>
          <p className="settings-error">Update failed: {state.message}</p>
          <button className="settings-button" onClick={onCheck}>
            Try again
          </button>
        </>
      )}
    </section>
  );
}
