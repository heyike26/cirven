"""Acquire only the pinned public font originals; never used at runtime."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
COMMIT = "66928797f271fed5ee428544e7229685e2d90127"
BASE = f"https://raw.githubusercontent.com/google/fonts/{COMMIT}/ofl"
FAMILIES = {
    "notosanssc": {"filename": "NotoSansSC[wght].ttf", "alias": "Studio Sans SC"},
    "manrope": {"filename": "Manrope[wght].ttf", "alias": "Studio Manrope"},
}


def acquire() -> None:
    records = []
    for family, spec in FAMILIES.items():
        directory = ROOT / "upstream" / family
        directory.mkdir(parents=True, exist_ok=True)
        for name in (spec["filename"], "OFL.txt", "METADATA.pb"):
            url = f"{BASE}/{family}/{quote(name)}"
            limit = 32 * 1024 * 1024 if name.endswith(".ttf") else 64 * 1024
            target = directory / name
            if target.exists():
                raise FileExistsError(target)
            with urlopen(Request(url, headers={"User-Agent": "EEAS-offline-font-assets"}), timeout=60) as response:
                if urlsplit(response.url).hostname != "raw.githubusercontent.com":
                    raise ValueError("unexpected font source host")
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise ValueError("font source exceeds bounded acquisition limit")
                final_url = response.url
                status = response.status
            with target.open("xb") as handle:
                handle.write(raw)
            records.append({"path": target.relative_to(ROOT).as_posix(), "url": url,
                            "final_url": final_url, "http_status": status,
                            "byte_length": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                            "acquired_at": datetime.now(timezone.utc).isoformat()})
            print(f"acquired {family}/{name}: {len(raw)} bytes", flush=True)
    manifest = {"schema": "eeas.offline-font-sources.v1", "repository": "https://github.com/google/fonts",
                "commit": COMMIT, "families": FAMILIES, "files": records,
                "limits": "Public official files only; acquisition and hashes do not establish Windows rendering qualification."}
    with (ROOT / "sources.json").open("x", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


if __name__ == "__main__":
    acquire()
