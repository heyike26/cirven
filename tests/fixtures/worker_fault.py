"""Original fault-injection controller. Runner/SQLite remain real."""
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker

directory, window = Path(sys.argv[1]), sys.argv[2]


class FaultController(Worker):
    def finish(self, *args, **kwargs):
        if window == "result-lost":
            with (directory / "fault-ready").open("xb") as stream:
                stream.write(b"real-runner-finished-before-database-terminal")
                stream.flush()
                os.fsync(stream.fileno())
            while True:
                time.sleep(0.05)
        super().finish(*args, **kwargs)


worker = FaultController(TaskService(directory / "eeas.sqlite3", ROOT), directory)
if window == "before-claim":
    (directory / "fault-ready").write_text("accepted-but-unclaimed")
else:
    assert worker.start()
while True:
    time.sleep(0.05)
