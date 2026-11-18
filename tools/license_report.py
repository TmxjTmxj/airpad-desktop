"""Collect license metadata for every pinned dependency (local, no network)."""
import importlib.metadata as md
from pathlib import Path

root = Path(__file__).resolve().parents[1]
names = [line.split("==")[0].strip() for line in (root / "requirements-lock.txt").read_text(encoding="utf-8").splitlines() if line.strip()]

for name in names:
    try:
        meta = md.metadata(name)
        version = md.version(name)
    except Exception:
        print(f"{name}\tNOT-INSTALLED\t-\t-")
        continue
    license_field = " ".join((meta.get("License") or "").split())
    if len(license_field) > 70:
        license_field = license_field[:67] + "..."
    classifiers = sorted({c.split("::")[-1].strip() for c in (meta.get_all("Classifier") or []) if c.startswith("License ::")})
    print(f"{name}\t{version}\t{license_field or '-'}\t{'; '.join(classifiers) or '-'}")
