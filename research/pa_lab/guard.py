"""The seal. Every file read in the lab goes through read_frame(); it refuses any file in data/sealed/ and any
frame holding a session dated in the final window, unless an explicit unlock is on: the named step
(unlock("final_test")) AND the environment variable PA_LAB_UNLOCK=final_test. Every unlock and relock is
written to results/access_log.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from . import config as C

ALLOWED_STEP = "final_test"
_state = {"step": None}


class SealError(RuntimeError):
    pass


def _log(event: str, step: str | None, note: str = "") -> None:
    C.ACCESS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(C.ACCESS_LOG, "a") as f:
        f.write(json.dumps({"t": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event,
                            "step": step, "demo": C.demo(), "note": note}) + "\n")


def unlocked() -> bool:
    return _state["step"] == ALLOWED_STEP and os.environ.get("PA_LAB_UNLOCK") == ALLOWED_STEP


@contextmanager
def unlock(step: str, note: str = ""):
    if step != ALLOWED_STEP or os.environ.get("PA_LAB_UNLOCK") != step:
        _log("unlock_refused", step, note)
        raise SealError(f"unlock refused: step must be '{ALLOWED_STEP}' and PA_LAB_UNLOCK must equal it")
    _state["step"] = step
    _log("unlock", step, note)
    try:
        yield
    finally:
        _state["step"] = None
        _log("relock", step, note)


def _is_sealed_path(p: Path) -> bool:
    try:
        Path(p).resolve().relative_to(C.SEALED.resolve())
        return True
    except ValueError:
        return False


def check_frame(df: pd.DataFrame, where: str = "") -> pd.DataFrame:
    if "session" in df.columns and len(df) and not unlocked():
        if (df["session"].astype(str) >= C.FINAL_FROM).any():
            raise SealError(f"final-window sessions in {where or 'a frame'} while the seal is closed")
    return df


def read_frame(path: Path | str) -> pd.DataFrame:
    path = Path(path)
    if _is_sealed_path(path) and not unlocked():
        raise SealError(f"read of sealed file {path.name} while the seal is closed")
    df = pd.read_parquet(path)
    return check_frame(df, path.name)


def write_sealed(df: pd.DataFrame, name: str) -> str:
    """Only the ingest calls this. Returns the file's sha256."""
    C.SEALED.mkdir(parents=True, exist_ok=True)
    p = C.SEALED / name
    df.to_parquet(p, index=False)
    return sha256(p)


def sha256(path: Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
