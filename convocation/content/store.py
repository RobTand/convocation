"""Git-backed content store. Every change is a commit."""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import frontmatter
from git import Repo

from convocation.config import settings


class ContentStore:
    """Manages content as markdown+frontmatter files in a git repository."""

    CONTENT_TYPES = ("announcements", "pages", "events", "members")

    def __init__(self, repo_path: Path | None = None):
        self.repo_path = repo_path or settings.content_abs_path
        self._ensure_repo()

    def _ensure_repo(self):
        if not (self.repo_path / ".git").exists():
            self.repo_path.mkdir(parents=True, exist_ok=True)
            self.repo = Repo.init(self.repo_path)
            # Create directory structure
            for ct in self.CONTENT_TYPES:
                (self.repo_path / ct).mkdir(exist_ok=True)
                (self.repo_path / ct / ".gitkeep").touch()
            # Initial commit
            self.repo.index.add(".")
            self.repo.index.commit("Initialize content repository")
        else:
            self.repo = Repo(self.repo_path)

    def _file_path(self, content_type: str, slug: str) -> Path:
        return self.repo_path / content_type / f"{slug}.md"

    def create(
        self, content_type: str, slug: str, metadata: dict[str, Any], body: str, author: str
    ) -> str:
        """Create new content. Returns commit SHA."""
        if content_type not in self.CONTENT_TYPES:
            raise ValueError(f"Invalid content type: {content_type}")

        fp = self._file_path(content_type, slug)
        if fp.exists():
            raise FileExistsError(f"Content already exists: {content_type}/{slug}")

        metadata["created_at"] = datetime.now(timezone.utc).isoformat()
        metadata["updated_at"] = metadata["created_at"]
        metadata["author"] = author

        post = frontmatter.Post(body, **metadata)
        fp.write_text(frontmatter.dumps(post), encoding="utf-8")

        self.repo.index.add([str(fp.relative_to(self.repo_path))])
        commit = self.repo.index.commit(f"Create {content_type}/{slug} by {author}")
        return commit.hexsha

    def update(
        self, content_type: str, slug: str, metadata: dict[str, Any] | None, body: str | None, author: str
    ) -> str:
        """Update existing content. Returns commit SHA."""
        fp = self._file_path(content_type, slug)
        if not fp.exists():
            raise FileNotFoundError(f"Content not found: {content_type}/{slug}")

        post = frontmatter.load(str(fp))

        if metadata:
            for k, v in metadata.items():
                post.metadata[k] = v
        if body is not None:
            post.content = body

        post.metadata["updated_at"] = datetime.now(timezone.utc).isoformat()
        post.metadata["updated_by"] = author

        fp.write_text(frontmatter.dumps(post), encoding="utf-8")

        self.repo.index.add([str(fp.relative_to(self.repo_path))])
        commit = self.repo.index.commit(f"Update {content_type}/{slug} by {author}")
        return commit.hexsha

    def delete(self, content_type: str, slug: str, author: str) -> str:
        """Delete content. Returns commit SHA."""
        fp = self._file_path(content_type, slug)
        if not fp.exists():
            raise FileNotFoundError(f"Content not found: {content_type}/{slug}")

        rel = str(fp.relative_to(self.repo_path))
        self.repo.index.remove([rel], working_tree=True)
        commit = self.repo.index.commit(f"Delete {content_type}/{slug} by {author}")
        return commit.hexsha

    def get(self, content_type: str, slug: str) -> dict[str, Any] | None:
        """Read a single content item."""
        fp = self._file_path(content_type, slug)
        if not fp.exists():
            return None

        post = frontmatter.load(str(fp))
        return {"slug": slug, "content_type": content_type, "metadata": dict(post.metadata), "body": post.content}

    def list_content(self, content_type: str) -> list[dict[str, Any]]:
        """List all content of a type."""
        if content_type not in self.CONTENT_TYPES:
            raise ValueError(f"Invalid content type: {content_type}")

        items = []
        content_dir = self.repo_path / content_type
        for fp in sorted(content_dir.glob("*.md")):
            post = frontmatter.load(str(fp))
            items.append({
                "slug": fp.stem,
                "content_type": content_type,
                "metadata": dict(post.metadata),
                "body": post.content,
            })

        # Sort by created_at descending
        items.sort(key=lambda x: x["metadata"].get("created_at", ""), reverse=True)
        return items

    def diff_preview(self, content_type: str, slug: str, new_metadata: dict | None, new_body: str | None) -> str:
        """Generate a diff preview of proposed changes without committing."""
        fp = self._file_path(content_type, slug)
        if not fp.exists():
            # New file — show full content as addition
            post = frontmatter.Post(new_body or "", **(new_metadata or {}))
            new_text = frontmatter.dumps(post)
            lines = new_text.splitlines(keepends=True)
            return "".join(f"+{line}" for line in lines)

        old_text = fp.read_text(encoding="utf-8")

        post = frontmatter.load(str(fp))
        if new_metadata:
            for k, v in new_metadata.items():
                post.metadata[k] = v
        if new_body is not None:
            post.content = new_body
        new_text = frontmatter.dumps(post)

        # Use git-style unified diff
        import difflib
        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{content_type}/{slug}.md",
            tofile=f"b/{content_type}/{slug}.md",
        )
        return "".join(diff)

    def get_history(self, content_type: str | None = None, slug: str | None = None, limit: int = 50) -> list[dict]:
        """Get commit history, optionally filtered by content path."""
        kwargs: dict[str, Any] = {"max_count": limit}
        if content_type and slug:
            kwargs["paths"] = [f"{content_type}/{slug}.md"]
        elif content_type:
            kwargs["paths"] = [content_type]

        commits = list(self.repo.iter_commits("HEAD", **kwargs))
        return [
            {
                "sha": c.hexsha,
                "short_sha": c.hexsha[:8],
                "message": c.message.strip(),
                "author": str(c.author),
                "timestamp": datetime.fromtimestamp(c.committed_date, tz=timezone.utc).isoformat(),
            }
            for c in commits
        ]

    def revert(self, commit_sha: str, author: str) -> str:
        """Revert a specific commit. Returns new commit SHA."""
        commit = self.repo.commit(commit_sha)
        self.repo.git.revert(commit_sha, no_edit=True)
        revert_commit = self.repo.head.commit
        return revert_commit.hexsha

    def get_commit_diff(self, commit_sha: str) -> str:
        """Get the diff for a specific commit."""
        commit = self.repo.commit(commit_sha)
        if commit.parents:
            return self.repo.git.diff(commit.parents[0].hexsha, commit.hexsha)
        else:
            return self.repo.git.show(commit.hexsha, format="", p=True)

    def export_bundle(self, output_path: Path) -> Path:
        """Create a git bundle of the entire content repo for export."""
        bundle_path = output_path / "content-repo.bundle"
        self.repo.git.bundle("create", str(bundle_path), "--all")
        return bundle_path
