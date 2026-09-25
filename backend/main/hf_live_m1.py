"""Upload the latest intraday M1 parquet snapshot to Hugging Face each minute."""

from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).parent.parent
load_dotenv(_ROOT / ".env", override=True)


@dataclass(frozen=True)
class _Snapshot:
    date: str
    minute: str
    content: bytes
    digest: str


class LiveM1HfUploader:
    """Single-worker uploader that never blocks the realtime collector.

    Only the newest waiting snapshot is retained while an upload is active. The
    remote path is stable, so consumers always see the most recently completed
    full-day parquet instead of having to combine hundreds of minute shards.
    """

    def __init__(self) -> None:
        self.repo_id = os.environ.get("HF_REPO_ID", "").strip()
        self.token = os.environ.get("HF_TOKEN", "").strip()
        self.enabled = os.environ.get("HF_LIVE_M1_UPLOAD", "1").strip().lower() not in {"0", "false", "no", "off"}
        self.retry_seconds = max(5, int(os.environ.get("HF_LIVE_M1_RETRY_SECONDS", "30")))
        self._pending: _Snapshot | None = None
        self._last_uploaded_digest = ""
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._started = False
        self._api = None

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.repo_id and self.token)

    def enqueue(self, path: Path, minute: str) -> None:
        if not self.available or not path.exists():
            return
        try:
            content = path.read_bytes()
        except OSError as exc:
            print(f"[HF M1] 讀取 {path.name} 失敗: {exc}", flush=True)
            return
        if not content:
            return
        digest = hashlib.sha256(content).hexdigest()
        with self._lock:
            if digest == self._last_uploaded_digest or (self._pending and digest == self._pending.digest):
                return
            self._pending = _Snapshot(path.stem, minute, content, digest)
            if not self._started:
                self._started = True
                threading.Thread(target=self._run, name="hf-live-m1", daemon=True).start()
        self._event.set()

    def _next_snapshot(self) -> _Snapshot | None:
        with self._lock:
            snapshot = self._pending
            self._pending = None
            return snapshot

    def _restore_after_failure(self, snapshot: _Snapshot) -> None:
        with self._lock:
            if self._pending is None:
                self._pending = snapshot

    def _upload(self, snapshot: _Snapshot) -> None:
        if self._api is None:
            from huggingface_hub import HfApi

            self._api = HfApi(token=self.token)
        path_in_repo = f"db/m1_live/{snapshot.date}.parquet"
        self._api.upload_file(
            path_or_fileobj=snapshot.content,
            path_in_repo=path_in_repo,
            repo_id=self.repo_id,
            repo_type="dataset",
            token=self.token,
            commit_message=f"Update live M1 {snapshot.minute}",
        )

    def _run(self) -> None:
        while True:
            self._event.wait()
            self._event.clear()
            while snapshot := self._next_snapshot():
                try:
                    self._upload(snapshot)
                except Exception as exc:
                    print(f"[HF M1] {snapshot.minute} 上傳失敗，{self.retry_seconds} 秒後重試: {exc}", flush=True)
                    self._restore_after_failure(snapshot)
                    time.sleep(self.retry_seconds)
                else:
                    with self._lock:
                        self._last_uploaded_digest = snapshot.digest
                    print(f"[HF M1] 已上傳 {snapshot.minute} -> db/m1_live/{snapshot.date}.parquet", flush=True)


_UPLOADER = LiveM1HfUploader()


def enqueue_live_m1_upload(path: Path, minute: str) -> None:
    """Queue an immutable copy of the current daily parquet for HF upload."""
    _UPLOADER.enqueue(path, minute)
