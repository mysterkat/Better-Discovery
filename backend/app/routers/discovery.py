"""Discovery router.

POST /discovery/start                 -> kick off pattern_discovery_v6.main()
GET  /discovery/status/{job_id}
GET  /discovery/results/{job_id}
GET  /discovery/defaults              -> current overridable constant values
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from ..bridge import discovery as disc_bridge
from ..hypothesis.models import HypothesisDiscoveryRequest
from ..hypothesis.service import HypothesisResearchService
from ..jobs.manager import JOBS
from ..jobs.runners import run_in_thread
from ..paths import DEFAULT_DISC_OUTPUT, USER_DATA
from ..schemas.common import JobRef
from ..schemas.discovery import DiscoveryStartRequest

router = APIRouter()
HYPOTHESIS = HypothesisResearchService()


@router.get("/discovery/defaults")
def discovery_defaults() -> dict:
    vals = disc_bridge.list_defaults()
    return {k: (str(v) if not isinstance(v, (int, float, str, bool, list, dict)) else v) for k, v in vals.items()}


@router.get("/discovery/params")
def discovery_params() -> list:
    """Return values + full UI metadata (type, group, min/max/step) for every
    overridable constant. The frontend uses this to render the settings form."""
    return disc_bridge.list_defaults_with_meta()


@router.post("/discovery/start", response_model=JobRef)
def discovery_start(req: DiscoveryStartRequest) -> JobRef:
    overrides = dict(req.overrides)
    mode = str(overrides.get("engine", "")).lower()
    if mode == "hypothesis" or "dataset_id" in overrides:
        overrides.pop("engine", None)
        try:
            request = HypothesisDiscoveryRequest.model_validate(overrides)
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()) from exc
        job = JOBS.create(
            kind="hypothesis_discovery",
            meta={
                "dataset_id": request.dataset_id,
                "symbol": request.symbol,
                "timeframe": request.timeframe,
                "max_variants": request.max_variants,
                "target_profit_pct": request.challenge.target_profit_pct,
                "max_attempt_days": request.challenge.max_attempt_days,
            },
        )
        run_in_thread(job, lambda: HYPOTHESIS.run_discovery(request))
        return JobRef(job_id=job.job_id, status=job.status)
    job = JOBS.create(kind="discovery", meta={"overrides": req.overrides})
    run_in_thread(job, lambda: disc_bridge.run_discovery(overrides))
    return JobRef(job_id=job.job_id, status=job.status)


@router.get("/discovery/status/{job_id}")
def discovery_status(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")
    return job.snapshot()


@router.get("/discovery/results/{job_id}", response_model=JobRef)
def discovery_results(job_id: str) -> JobRef:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job_id {job_id}")
    return JobRef(job_id=job.job_id, status=job.status, result=job.result, error=job.error)


@router.get("/discovery/set-file")
def discovery_set_file(path: str) -> dict:
    """Return the contents of a .set file produced by a discovery run.

    Security: the file path must resolve INSIDE one of the known-safe roots
    so the endpoint can't be abused to read arbitrary files.

    v1.1.3 — allowed roots are:
      * `DEFAULT_DISC_OUTPUT`          — the app's default discovery output
      * `USER_DATA`                    — the broader app-writable area
      * the toolkit module's current `OUTPUT_FOLDER` — handles user overrides
        that point outside USER_DATA (e.g. a custom folder on the desktop)

    Previously this used `str(resolved).startswith(str(safe_root))` against
    only DEFAULT_DISC_OUTPUT — fragile on Windows (case, trailing-sep) AND
    refused legitimate paths when the user overrode OUTPUT_FOLDER.
    """
    resolved = Path(path).resolve()

    # Build the set of allowed roots (resolved + normalised).
    allowed_roots: list[Path] = [
        DEFAULT_DISC_OUTPUT.resolve(),
        USER_DATA.resolve(),
    ]
    # Pull the current OUTPUT_FOLDER from the toolkit module so any custom
    # override from the most recent / current run is also trusted.
    try:
        mod_out = getattr(disc_bridge._get_module(), "OUTPUT_FOLDER", None)
        if mod_out:
            allowed_roots.append(Path(str(mod_out)).resolve())
    except Exception:
        pass  # module load failure → fall back to the static roots above

    def _inside(p: Path, root: Path) -> bool:
        try:
            p.relative_to(root)
            return True
        except ValueError:
            return False

    if not any(_inside(resolved, r) for r in allowed_roots):
        raise HTTPException(
            403,
            f"path outside any allowed discovery output folder: {path}",
        )
    if not resolved.is_file() or resolved.suffix != ".set":
        raise HTTPException(404, f".set file not found: {path}")
    try:
        return {
            "path": str(resolved),
            "name": resolved.name,
            "content": resolved.read_text(encoding="utf-8", errors="replace"),
        }
    except OSError as e:
        raise HTTPException(500, f"could not read .set: {e}") from e
