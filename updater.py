"""
Auto-updater — downloads latest version from GitHub directly.
No git required on the user's machine.

Uses the SHA-specific archive URL (`/archive/{sha}.zip`) instead of
`/archive/refs/heads/main.zip` so we are immune to GitHub's CDN cache.
After extraction, purges all `__pycache__` directories so a stale
`.pyc` can never shadow a newly-written `.py`.
"""
import urllib.request
import urllib.error
import zipfile
import shutil
import sys
import os
import json

REPO = "Itz-Lyra/ClubGG-Bot"
COMMITS_API = f"https://api.github.com/repos/{REPO}/commits/main"
# SHA-specific archive URL: content-addressable, never stale.
ARCHIVE_TPL = f"https://github.com/{REPO}/archive/{{sha}}.zip"
VERSION_FILE = os.path.join(os.path.dirname(__file__), ".version")
LOG_FILE = os.path.join(os.path.dirname(__file__), "updater.log")


def _log(msg: str) -> None:
    """Print and append to updater.log so failures are visible after restart."""
    line = f"[updater] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _get_remote_version() -> str:
    # Cache-bust the API call — GitHub's API can briefly cache HEAD
    req = urllib.request.Request(
        COMMITS_API,
        headers={
            "User-Agent":     "ClubGG-Bot-Updater",
            "Cache-Control":  "no-cache",
            "Pragma":         "no-cache",
        },
    )
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())
        return data["sha"]


def _get_local_version() -> str:
    if os.path.exists(VERSION_FILE):
        return open(VERSION_FILE).read().strip()
    return ""


def _save_version(sha: str) -> None:
    with open(VERSION_FILE, "w") as f:
        f.write(sha)


def _purge_pycache(root: str) -> int:
    """Recursively remove __pycache__ dirs and stray .pyc files. Returns count removed."""
    removed = 0
    for dirpath, dirnames, filenames in os.walk(root):
        # Remove __pycache__ subdirs
        for d in list(dirnames):
            if d == "__pycache__":
                full = os.path.join(dirpath, d)
                try:
                    shutil.rmtree(full)
                    dirnames.remove(d)
                    removed += 1
                except OSError as e:
                    _log(f"could not remove {full}: {e}")
        # Remove any stray .pyc next to source
        for f in filenames:
            if f.endswith(".pyc"):
                full = os.path.join(dirpath, f)
                try:
                    os.unlink(full)
                    removed += 1
                except OSError:
                    pass
    return removed


def check_and_update():
    _log("Checking for updates...")
    try:
        remote = _get_remote_version()
        local  = _get_local_version()
        _log(f"local SHA  = {local[:8] if local else '(none)'}")
        _log(f"remote SHA = {remote[:8]}")

        if remote == local:
            _log("Already up to date.")
            return

        download_url = ARCHIVE_TPL.format(sha=remote)
        _log(f"Update available — downloading {download_url}")

        # Download zip
        base_dir = os.path.dirname(os.path.abspath(__file__))
        tmp_zip  = os.path.join(base_dir, "_update.zip")
        req = urllib.request.Request(
            download_url,
            headers={
                "User-Agent":    "ClubGG-Bot-Updater",
                "Cache-Control": "no-cache",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp_zip, "wb") as f:
            shutil.copyfileobj(r, f)

        zip_size = os.path.getsize(tmp_zip)
        _log(f"Downloaded {zip_size} bytes — extracting")

        # Extract and overwrite — skip .version, config.json, runtime caches
        skip = {".version", "config.json", "venv", "updater.log"}
        files_written = 0

        with zipfile.ZipFile(tmp_zip, "r") as z:
            for member in z.namelist():
                # Strip the top-level folder (ClubGG-Bot-{sha}/)
                parts = member.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel = parts[1]
                # Skip protected files / dirs
                if any(rel == s or rel.startswith(s + "/") for s in skip):
                    continue
                dest = os.path.join(base_dir, rel)
                if member.endswith("/"):
                    os.makedirs(dest, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest), exist_ok=True)
                    with z.open(member) as src, open(dest, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    files_written += 1

        os.unlink(tmp_zip)
        _log(f"Extracted {files_written} files")

        # Purge __pycache__ so Python can't load stale .pyc shadowing new .py
        purged = _purge_pycache(base_dir)
        _log(f"Purged {purged} pycache entries")

        _save_version(remote)
        _log(f"Updated to {remote[:8]} — restarting...")
        os.execv(sys.executable, [sys.executable] + sys.argv)

    except Exception as e:
        _log(f"Update FAILED: {type(e).__name__}: {e}")
        _log("continuing with current version")


if __name__ == "__main__":
    check_and_update()
