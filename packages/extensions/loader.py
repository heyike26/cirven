"""Only invoked after exact-byte trusted fixture checks by ExtensionRegistry."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

path, mode, text = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.dont_write_bytecode = True
spec = importlib.util.spec_from_file_location("eeas_original_fixture", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
result = module.initialize() if mode == "inspect" else module.invoke(text)
print(json.dumps({"loaded_from": str(path), "file_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "result": result}, ensure_ascii=False))
