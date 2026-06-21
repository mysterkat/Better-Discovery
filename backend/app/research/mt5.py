"""Headless MetaEditor and MT5 Strategy Tester integration for Windows."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from ..bridge import set_to_mql
from ..paths import DEFAULT_RESEARCH
from .models import BacktestSpec, MT5Environment, StrategySpec


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    encodings = ("utf-16-le", "utf-8-sig", "utf-8") if b"\x00" in raw[:200] else ("utf-8-sig", "utf-8")
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


class MT5Worker:
    def __init__(self, environment: MT5Environment | None = None) -> None:
        self.environment = environment or self.preferred_environment()
        self.terminal = Path(self.environment.terminal_path)
        self.metaeditor = Path(self.environment.metaeditor_path)
        self.data_path = self._resolve_data_path(self.environment.data_path)

    @staticmethod
    def preferred_environment() -> MT5Environment:
        manifest = DEFAULT_RESEARCH / "portable_mt5.json"
        if manifest.is_file():
            import json

            return MT5Environment(**json.loads(manifest.read_text(encoding="utf-8")))
        return MT5Environment()

    @staticmethod
    def _resolve_data_path(configured: str | None) -> Path:
        if configured:
            path = Path(configured).resolve()
            if not (path / "MQL5").is_dir():
                raise FileNotFoundError(f"MT5 data path has no MQL5 folder: {path}")
            return path
        root = Path(os.environ.get("APPDATA", Path.home())) / "MetaQuotes" / "Terminal"
        candidates = [p for p in root.glob("*") if (p / "MQL5").is_dir()]
        if not candidates:
            raise FileNotFoundError(
                "No MT5 data directory found; set data_path in the research request"
            )
        candidates.sort(key=lambda p: (p / "MQL5").stat().st_mtime, reverse=True)
        return candidates[0]

    def status(self) -> dict[str, Any]:
        return {
            "terminal_path": str(self.terminal),
            "terminal_exists": self.terminal.is_file(),
            "metaeditor_path": str(self.metaeditor),
            "metaeditor_exists": self.metaeditor.is_file(),
            "data_path": str(self.data_path),
            "experts_path": str(self.data_path / "MQL5" / "Experts"),
            "terminal_running": self._terminal_running(),
            "portable": self.environment.portable,
            "live_trading_supported": False,
        }

    def _terminal_running(self) -> bool:
        if os.name != "nt":
            return False
        command = (
            "$p=Get-CimInstance Win32_Process -Filter \"Name='terminal64.exe'\" "
            "-ErrorAction SilentlyContinue; $p | ForEach-Object {$_.ExecutablePath}"
        )
        detailed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
        paths = [Path(line.strip()) for line in detailed.stdout.splitlines() if line.strip()]
        if paths:
            target = str(self.terminal.resolve()).lower()
            return any(str(path.resolve()).lower() == target for path in paths)
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq terminal64.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return '"terminal64.exe"' in result.stdout.lower()

    def _start_local_agent(self) -> subprocess.Popen | None:
        if not self.environment.portable:
            return None
        secrets_path = DEFAULT_RESEARCH / "portable_mt5_secrets.json"
        if not secrets_path.is_file():
            raise RuntimeError(
                "portable MT5 local-agent credential is missing; rerun portable setup"
            )
        import json

        password = json.loads(secrets_path.read_text(encoding="utf-8")).get(
            "local_agent_password"
        )
        if not password:
            raise RuntimeError("portable MT5 local-agent credential is empty")
        try:
            with socket.create_connection(("127.0.0.1", 3000), timeout=0.2):
                return None
        except OSError:
            pass
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [
                str(self.terminal.parent / "metatester64.exe"),
                "/local",
                "/address:127.0.0.1:3000",
                f"/password:{password}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("portable MT5 local tester agent exited during startup")
            try:
                with socket.create_connection(("127.0.0.1", 3000), timeout=0.2):
                    return proc
            except OSError:
                time.sleep(0.2)
        proc.terminate()
        raise RuntimeError("portable MT5 local tester agent did not open port 3000")

    @staticmethod
    def _stop_local_agent(proc: subprocess.Popen | None) -> None:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

    def generate_ea(self, strategy: StrategySpec) -> dict[str, str]:
        source_set = Path(strategy.source_set_path)
        experts_dir = self.data_path / "MQL5" / "Experts" / "BetterDiscoveryResearch"
        experts_dir.mkdir(parents=True, exist_ok=True)
        installed_source = experts_dir / f"{strategy.name}.mq5"
        generated = Path(
            set_to_mql.export_from_set_path(
                source_set,
                output_name=strategy.name,
                also_write_paths=[installed_source],
            )
        )
        tester_dir = self.data_path / "MQL5" / "Profiles" / "Tester"
        tester_dir.mkdir(parents=True, exist_ok=True)
        tester_set = tester_dir / f"{strategy.name}.set"
        shutil.copy2(source_set, tester_set)
        return {
            "generated_source": str(generated),
            "installed_source": str(installed_source),
            "tester_set": str(tester_set),
        }

    def compile(self, mq5_path: str | Path, timeout_seconds: int = 180) -> dict[str, Any]:
        source = Path(mq5_path).resolve()
        if not self.metaeditor.is_file():
            raise FileNotFoundError(f"MetaEditor not found: {self.metaeditor}")
        if not source.is_file():
            raise FileNotFoundError(f"EA source not found: {source}")
        log_dir = DEFAULT_RESEARCH / "artifacts" / "compile_logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{source.stem}_{int(time.time())}.log"
        ex5_path = source.with_suffix(".ex5")
        previous_mtime = ex5_path.stat().st_mtime_ns if ex5_path.is_file() else None
        proc = subprocess.run(
            [
                str(self.metaeditor),
                f"/compile:{source}",
                *(["/portable"] if self.environment.portable else []),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        log_text = _read_text(log_path) if log_path.is_file() else ""
        if not log_text:
            shared_log = self.data_path / "logs" / "metaeditor.log"
            if shared_log.is_file():
                source_key = str(source).lower()
                matches = [
                    line for line in _read_text(shared_log).splitlines()
                    if source_key in line.lower() and "compile" in line.lower()
                ]
                log_text = matches[-1] if matches else ""
                log_path.write_text(log_text + "\n", encoding="utf-8")
        binary_changed = ex5_path.is_file() and (
            previous_mtime is None or ex5_path.stat().st_mtime_ns != previous_mtime
        )
        success = binary_changed and "0 errors" in log_text.lower()
        return {
            "success": success,
            "return_code": proc.returncode,
            "source_path": str(source),
            "binary_path": str(ex5_path),
            "log_path": str(log_path),
            "log_tail": "\n".join(log_text.splitlines()[-20:]),
        }

    def _write_tester_config(
        self,
        strategy: StrategySpec,
        spec: BacktestSpec,
        report_path: Path,
    ) -> Path:
        # MT5 corrupts /config arguments whose path contains spaces. Keep
        # configs in the terminal data directory for every environment.
        config_dir = self.data_path / "research_configs"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_path = config_dir / f"{strategy.name}_{int(time.time() * 1000)}.ini"
        expert = rf"BetterDiscoveryResearch\{strategy.name}.ex5"
        # MT5 resolves Report relative to the platform installation directory
        # and silently ignores absolute paths. A relative path may traverse to
        # our writable staging directory.
        report_value = os.path.relpath(report_path, self.terminal.parent)
        report_value = str(Path(report_value).with_suffix(""))
        lines = [
            "[Tester]",
            f"Expert={expert}",
            f"ExpertParameters={strategy.name}.set",
            f"Symbol={spec.symbol}",
            f"Period={spec.timeframe}",
            f"Model={spec.model}",
            f"FromDate={spec.date_from:%Y.%m.%d}",
            f"ToDate={spec.date_to:%Y.%m.%d}",
            "ForwardMode=0",
            f"Deposit={spec.deposit:.2f}",
            f"Currency={spec.currency}",
            f"Leverage=1:{spec.leverage}",
            f"ExecutionMode={spec.execution_mode}",
            "Optimization=0",
            "Visual=0",
            f"Report={report_value}",
            "ReplaceReport=1",
            "ShutdownTerminal=1",
            "UseLocal=1",
            "UseRemote=0",
            "UseCloud=0",
        ]
        config_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return config_path

    def backtest(self, strategy: StrategySpec, spec: BacktestSpec) -> dict[str, Any]:
        if not self.terminal.is_file():
            raise FileNotFoundError(f"MT5 terminal not found: {self.terminal}")
        if self._terminal_running():
            raise RuntimeError(
                "MT5 terminal64.exe is already running. Close it before headless testing "
                "or configure a dedicated MT5 tester installation. The worker will not "
                "risk attaching to or shutting down an interactive terminal."
            )
        reports = DEFAULT_RESEARCH / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        report_name = (
            f"{strategy.name}_{spec.symbol}_{spec.timeframe}_"
            f"{spec.date_from:%Y%m%d}_{spec.date_to:%Y%m%d}_{int(time.time() * 1000)}.htm"
        )
        # MT5 command-line reports are native .htm files. Portable MT5 accepts
        # a bare filename in its writable platform root most reliably.
        if self.environment.portable:
            mt5_report_path = self.terminal.parent / report_name
        else:
            staging_dir = self.data_path / "reports"
            staging_dir.mkdir(parents=True, exist_ok=True)
            mt5_report_path = staging_dir / report_name
        archive_path = reports / report_name
        config_path = self._write_tester_config(strategy, spec, mt5_report_path)
        started = time.monotonic()
        started_wall = time.time()
        agent_proc = self._start_local_agent()
        proc = subprocess.Popen(
            [
                str(self.terminal),
                *(["/portable"] if self.environment.portable else []),
                f"/config:{config_path}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = started + spec.timeout_seconds
        exited_at: float | None = None
        found: Path | None = None
        while time.monotonic() < deadline:
            candidates = (
                mt5_report_path,
                self.data_path / report_name,
                self.terminal.parent / report_name,
                Path.cwd() / report_name,
            )
            found = next(
                (
                    path for path in candidates
                    if path.is_file()
                    and path.stat().st_size > 1000
                    and path.stat().st_mtime >= started_wall - 2
                ),
                None,
            )
            if found is not None:
                break
            if proc.poll() is not None:
                exited_at = exited_at or time.monotonic()
                # MT5 can flush the report shortly after the terminal process
                # reports exit. Give it a bounded grace period before failure.
                if time.monotonic() - exited_at >= 15:
                    checked = ", ".join(str(path) for path in candidates)
                    self._stop_local_agent(agent_proc)
                    raise RuntimeError(
                        f"MT5 exited with code {proc.returncode} without producing "
                        f"a native tester report. Checked: {checked}"
                    )
            time.sleep(1.0)
        else:
            if proc.poll() is None:
                proc.terminate()
            self._stop_local_agent(agent_proc)
            raise TimeoutError(f"MT5 backtest exceeded {spec.timeout_seconds}s")
        if proc.poll() is None:
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.terminate()
        assert found is not None
        if found.resolve() != archive_path.resolve():
            shutil.copy2(found, archive_path)
        self._stop_local_agent(agent_proc)
        return {
            "report_path": str(archive_path),
            "mt5_report_path": str(found),
            "config_path": str(config_path),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }


def bootstrap_portable_mt5(
    source_environment: MT5Environment | None = None,
    destination: str | Path | None = None,
    local_agent_password: str | None = None,
) -> dict[str, Any]:
    """Create a writable research-only MT5 installation.

    Market history is junctioned to the existing terminal to avoid duplicating
    several gigabytes. Runtime, config, EAs, indicators, and tester profiles are
    copied. No live order or deployment configuration is added.
    """
    source = MT5Worker(source_environment or MT5Environment())
    if source._terminal_running():
        raise RuntimeError("close the source MT5 terminal before portable setup")
    default_root = Path(os.environ.get("LOCALAPPDATA", Path.home()))
    target = Path(
        destination or default_root / "BetterDiscoveryResearch" / "mt5_portable"
    ).resolve()
    if " " in str(target):
        raise ValueError("portable MT5 destination cannot contain spaces (MetaEditor CLI limitation)")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source.terminal.parent, target, dirs_exist_ok=True)
    for folder in ("config", "MQL5", "Tester"):
        src = source.data_path / folder
        if src.is_dir():
            shutil.copytree(
                src,
                target / folder,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("Logs", "*.log"),
            )
    (target / "reports").mkdir(exist_ok=True)
    bases_link = target / "bases"
    if not bases_link.exists():
        result = subprocess.run(
            ["cmd.exe", "/c", "mklink", "/J", str(bases_link), str(source.data_path / "bases")],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"could not junction MT5 history: {result.stderr or result.stdout}")
    environment = MT5Environment(
        terminal_path=str(target / "terminal64.exe"),
        metaeditor_path=str(target / "MetaEditor64.exe"),
        data_path=str(target),
        portable=True,
    )
    import json

    manifest = DEFAULT_RESEARCH / "portable_mt5.json"
    manifest.write_text(environment.model_dump_json(indent=2), encoding="utf-8")
    if local_agent_password:
        secrets = DEFAULT_RESEARCH / "portable_mt5_secrets.json"
        secrets.write_text(
            json.dumps({"local_agent_password": local_agent_password}, indent=2),
            encoding="utf-8",
        )
    return {
        "environment": environment.model_dump(),
        "manifest": str(manifest),
        "history_source": str(source.data_path / "bases"),
    }
