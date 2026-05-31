"""Local path policy for private LMS state and generated artifacts."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PathPolicy:
    root: Path = Path(".")
    cache_dir: Path = Path(".cache/ku-lms-cli")
    private_dir: Path = Path("private")
    downloads_dir: Path = Path("downloads")
    discovery_dir: Path = Path("discovery")

    def resolve(self, path: Path) -> Path:
        return (self.root / path).resolve()

    def ensure(self) -> None:
        for path in (self.cache_dir, self.private_dir, self.downloads_dir, self.discovery_dir):
            self.resolve(path).mkdir(parents=True, exist_ok=True)

    def is_private_path(self, path: str | Path) -> bool:
        target = self.resolve(Path(path))
        private_roots = [self.resolve(p) for p in (self.cache_dir, self.private_dir, self.downloads_dir)]
        return any(target == root or root in target.parents for root in private_roots)
