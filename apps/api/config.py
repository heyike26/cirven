from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    worker_enabled: bool = False

    def __post_init__(self):
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("EEAS only supports a localhost bind")
        if not 1 <= self.port <= 65535:
            raise ValueError("Port must be between 1 and 65535")

    @classmethod
    def from_env(cls):
        return cls(Path(os.environ.get("EEAS_DATA_DIR", "var")).resolve(),
                   os.environ.get("EEAS_HOST", "127.0.0.1"),
                   int(os.environ.get("EEAS_PORT", "8765")),
                   os.environ.get("EEAS_WORKER", "1") == "1")

    @property
    def origin(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"
