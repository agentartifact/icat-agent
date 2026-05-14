from __future__ import annotations
import subprocess
import os
import logging
from pathlib import Path
from langchain_core.tools import tool

class RepoUtilityAgent:
    """
    Agent responsible for cloning the repository, checking out the commit,
    and preparing the repository structure and file list.
    """
    def __init__(self, repo_url: str, commit: str, workdir: str):
        self.repo_url = repo_url
        self.commit = commit
        self.repo_path = Path(workdir) / Path(repo_url).name.replace("/", "_")
        self.log = logging.getLogger("RepoUtilityAgent")

    @tool
    def clone_repo(repo_url: str, dest: str):
        """Clone a git repository to a destination directory."""
        if os.path.exists(dest):
            return f"Exists: {dest}"
        subprocess.run(["git", "clone", repo_url, dest], check=True)
        return f"Cloned {repo_url}"

    @tool
    def checkout_commit(repo_path: str, commit: str):
        """Checkout a specific commit in a git repository."""
        subprocess.run(["git", "-C", repo_path, "fetch"], check=True)
        # Reset any local changes and clean untracked files
        subprocess.run(["git", "-C", repo_path, "reset", "--hard"], check=True)
        subprocess.run(["git", "-C", repo_path, "clean", "-fd"], check=True)
        subprocess.run(["git", "-C", repo_path, "checkout", commit], check=True)
        return f"Checked out {commit}"

    @tool
    def list_files(repo_path: str):
        """List all files tracked by git in the repository."""
        res = subprocess.run(["git", "-C", repo_path, "ls-files"], capture_output=True, text=True)
        return res.stdout.strip().splitlines()

    def build_structure(self, files: list[str]) -> dict:
        """
        Build a nested dictionary structure from a list of file paths.
        Directories are represented as dictionaries, files as None.
        """
        root = {}
        for f in files:
            parts = f.split("/")
            cur = root
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = None
        return root

    def run(self) -> dict:
        """
        Clone the repository, checkout the commit, and return repo metadata.

        Returns:
            dict with keys: repo_path, repo_files, repo_structure
        """
        url = self.repo_url
        dest = str(self.repo_path)

        # convert github repo short name to full URL
        if not url.startswith("http") and "/" in url:
            url = f"https://github.com/{url}.git"

        # clone if not exists
        if not (Path(dest) / ".git").exists():
            self.log.info(f"Cloning {url} to {dest}")
            RepoUtilityAgent.clone_repo.invoke({"repo_url": url, "dest": dest})
        else:
            self.log.info(f"Repository already exists at {dest}")

        # Checkout the specific commit
        self.log.info(f"Checking out commit {self.commit}")
        RepoUtilityAgent.checkout_commit.invoke({"repo_path": dest, "commit": self.commit})

        # List files and build structure
        self.log.info("Building repository structure")
        files = RepoUtilityAgent.list_files.invoke({"repo_path": dest})
        struct = self.build_structure(files)

        self.log.info(f"Repository prepared: {len(files)} files")

        return {
            "repo_path": dest,
            "repo_files": files,
            "repo_structure": struct,
        }
