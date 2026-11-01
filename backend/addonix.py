#!/usr/bin/env python3
"""AddonixWoW backend: finds the WoW Forever AddOns folder and installs CurseForge addons.

Every command prints exactly one JSON object on stdout:
  {"ok": true, ...}  exit 0
  {"ok": false, "code": "...", "message": "...", "url": "..."}  exit 1
"""

import fcntl
import hashlib
import http.client
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from pathlib import Path

WOW_GAME_ID = 1
SORT_BY_POPULARITY = 2
PAGE_SIZE = 30
TIMEOUT_SECONDS = 20
RELEASE_TYPE_STABLE = 1
HASH_ALGORITHMS = {1: "sha1", 2: "md5"}
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 50_000
USER_AGENT = "AddonixWoW/0.1 (+https://github.com/aledx18/addonixwow)"
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
TRUSTED_KEY_DOMAINS = ("forgecdn.net", "curseforge.com")
COLOR_CODES = re.compile(r"\|c[0-9a-fA-F]{8}|\|r")


class AddonixError(Exception):
    def __init__(self, code, message, url=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.url = url


def config_path():
    root = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(root) / "addonixwow" / "config.json"


def state_path():
    root = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(root) / "addonixwow" / "state.json"


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_private_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    # A leftover temp file could carry wider permissions or be a symlink.
    temp.unlink(missing_ok=True)
    descriptor = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2)
    os.replace(temp, path)


def load_config():
    return read_json(config_path())


def load_state():
    return read_json(state_path())


def api_key(config):
    return os.environ.get("CURSEFORGE_API_KEY") or config.get("apiKey") or ""


# --- AddOns folder detection -------------------------------------------------
# Each detector returns candidate AddOns folders for one way of installing the
# Battle.net launcher. Support for another launcher is one more entry in DETECTORS.


def wow_prefixes(prefix_root):
    return sorted(prefix_root.glob("*/drive_c/Program Files*/World of Warcraft/_forever_"))


def detect_faugus():
    config = read_json(Path.home() / ".config" / "faugus-launcher" / "config.json")
    roots = []
    if config.get("default-prefix"):
        roots.append(Path(str(config["default-prefix"])).expanduser())
    roots.append(Path.home() / "Faugus")
    return [flavor / "Interface" / "AddOns" for root in roots for flavor in wow_prefixes(root)]


DETECTORS = (("faugus", detect_faugus),)


def resolve_addons_folder(config):
    if config.get("addonsPath"):
        return Path(str(config["addonsPath"])).expanduser(), "config"
    for source, detector in DETECTORS:
        for candidate in detector():
            if candidate.is_dir() or candidate.parent.is_dir():
                return candidate, source
    return None, "none"


# --- CurseForge client ---------------------------------------------------------


def api_base():
    return os.environ.get("ADDONIX_CF_BASE", "https://api.curseforge.com").rstrip("/")


def is_allowed_url(url):
    parsed = urllib.parse.urlparse(url)
    return parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS)


def is_key_host(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower()
    if host == urllib.parse.urlparse(api_base()).hostname:
        return True
    return any(host == domain or host.endswith("." + domain) for domain in TRUSTED_KEY_DOMAINS)


class SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_allowed_url(newurl):
            raise AddonixError("network", "Refused an insecure redirect.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_url(url, key):
    if not is_allowed_url(url):
        raise AddonixError("network", "Refused an insecure download URL.")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*"})
    if key and is_key_host(url):
        # Unredirected: the key must never follow a redirect to another host.
        request.add_unredirected_header("x-api-key", key)
    opener = urllib.request.build_opener(SafeRedirects)
    return opener.open(request, timeout=TIMEOUT_SECONDS)


def api_get(path, key, params=None):
    url = api_base() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        with open_url(url, key) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise AddonixError("invalid_key", "CurseForge rejected the API key.") from None
        if error.code == 404:
            raise AddonixError("not_found", "CurseForge has no such addon or file.") from None
        raise AddonixError("network", f"CurseForge returned HTTP {error.code}.") from None
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        raise AddonixError("network", f"Could not reach CurseForge: {getattr(error, 'reason', error)}") from None
    except ValueError:
        raise AddonixError("network", "CurseForge returned an unreadable response.") from None


def require_key(config):
    key = api_key(config)
    if not key:
        raise AddonixError("missing_key", "A CurseForge API key is required.")
    return key


def fetch_forever_type_id(key):
    types = api_get(f"/v1/games/{WOW_GAME_ID}/version-types", key).get("data") or []
    for entry in types:
        label = f"{entry.get('name', '')} {entry.get('slug', '')}".lower()
        if "forever" in label:
            return entry.get("id")
    return None


def forever_type_id(config, key):
    """Resolve the Forever version type. Never falls back to other flavors: their addons would break."""
    if config.get("gameVersionTypeId"):
        return config["gameVersionTypeId"]
    state = load_state()
    if state.get("gameVersionTypeId"):
        return state["gameVersionTypeId"]
    type_id = fetch_forever_type_id(key)
    if not type_id:
        raise AddonixError("flavor_unavailable", "CurseForge does not list WoW Forever as a game version yet.")
    state["gameVersionTypeId"] = type_id
    write_private_json(state_path(), state)
    return type_id


def choose_file(files):
    available = [item for item in files if item.get("isAvailable", True)]
    if not available:
        return None
    stable = [item for item in available if item.get("releaseType") == RELEASE_TYPE_STABLE]
    return max(stable or available, key=lambda item: item.get("fileDate") or "")


def file_download_url(mod_id, entry, key):
    if entry.get("downloadUrl"):
        return entry["downloadUrl"]
    try:
        return api_get(f"/v1/mods/{mod_id}/files/{entry['id']}/download-url", key).get("data")
    except AddonixError as error:
        if error.code in ("not_found", "invalid_key"):
            return None
        raise


def download(url, key, destination, entry):
    expected = next((h for h in entry.get("hashes") or [] if h.get("algo") in HASH_ALGORITHMS), None)
    digest = hashlib.new(HASH_ALGORITHMS[expected["algo"]]) if expected else None
    total = 0
    try:
        with open_url(url, key) as response, destination.open("wb") as output:
            while chunk := response.read(1024 * 256):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise AddonixError("invalid_archive", "The addon archive is too large.")
                output.write(chunk)
                if digest:
                    digest.update(chunk)
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
        raise AddonixError("network", f"Download failed: {getattr(error, 'reason', error)}") from None
    if entry.get("fileLength") and total != entry["fileLength"]:
        raise AddonixError("integrity", "The download is incomplete.")
    if digest and digest.hexdigest().lower() != str(expected.get("value", "")).lower():
        raise AddonixError("integrity", "The download failed its integrity check.")


# --- Archive handling ----------------------------------------------------------


def validate_archive(archive):
    """Return the top-level folders of an addon zip, rejecting anything that could escape AddOns."""
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES or sum(info.file_size for info in infos) > MAX_EXTRACTED_BYTES:
        raise AddonixError("invalid_archive", "The addon archive is too large.")
    folders = set()
    has_toc = False
    for info in infos:
        name = info.filename
        parts = [part for part in name.split("/") if part]
        # "." matters as much as "..": a "./" entry makes AddOns itself the swap target.
        unsafe = name.startswith("/") or "\\" in name or ".." in parts or "." in parts or (parts and ":" in parts[0])
        if unsafe or stat.S_ISLNK(info.external_attr >> 16):
            raise AddonixError("invalid_archive", f"The addon archive contains an unsafe entry: {name}")
        if not parts:
            continue
        if len(parts) == 1 and not info.is_dir():
            raise AddonixError("invalid_archive", "The addon archive has files outside an addon folder.")
        folders.add(parts[0])
        has_toc = has_toc or (len(parts) == 2 and parts[1].lower().endswith(".toc"))
    if not folders or not has_toc:
        raise AddonixError("invalid_archive", "The archive does not look like a WoW addon.")
    return sorted(folders)


def extract_archive(archive_path, extract_root):
    try:
        with zipfile.ZipFile(archive_path) as archive:
            folders = validate_archive(archive)
            archive.extractall(extract_root)
    except (zipfile.BadZipFile, zlib.error, EOFError, NotImplementedError, RuntimeError):
        raise AddonixError("invalid_archive", "The downloaded file is not a readable zip archive.") from None
    return folders


def swap_folders(folders, extract_root, addons, backup_root):
    """Move new folders into AddOns, restoring what was there if any step fails."""
    backup_root.mkdir()
    done = []
    try:
        for folder in folders:
            target = addons / folder
            backup = backup_root / folder
            had_previous = target.exists() or target.is_symlink()
            if had_previous:
                os.rename(target, backup)
            done.append((target, backup, had_previous))
            os.rename(extract_root / folder, target)
    except OSError as error:
        for target, backup, had_previous in reversed(done):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            if had_previous:
                os.rename(backup, target)
        raise AddonixError("io", f"Could not write to the AddOns folder: {error.strerror}") from None


def toc_metadata(folder):
    tocs = sorted(folder.glob("*.toc"))
    if not tocs:
        return None
    title = ""
    version = ""
    try:
        for line in tocs[0].read_text(encoding="utf-8-sig", errors="replace").splitlines():
            if line.startswith("## Title:") and not title:
                title = COLOR_CODES.sub("", line.split(":", 1)[1]).strip()
            elif line.startswith("## Version:") and not version:
                version = line.split(":", 1)[1].strip()
    except OSError:
        pass
    return {"name": title or folder.name, "version": version, "folders": [folder.name], "modId": None}


def list_installed(addons, state):
    if not addons or not addons.is_dir():
        return []
    managed = []
    claimed = set()
    for mod_id, mod in sorted((state.get("mods") or {}).items(), key=lambda item: item[1].get("name", "")):
        folders = [name for name in mod.get("folders", []) if (addons / name).is_dir()]
        if folders:
            managed.append({"name": mod.get("name", ""), "version": mod.get("version", ""), "folders": folders, "modId": int(mod_id)})
            claimed.update(folders)
    others = []
    for folder in sorted(addons.iterdir(), key=lambda path: path.name.lower()):
        if folder.is_dir() and folder.name not in claimed:
            metadata = toc_metadata(folder)
            if metadata:
                others.append(metadata)
    return managed + others


# --- Commands ------------------------------------------------------------------


def command_status(_args):
    config = load_config()
    addons, source = resolve_addons_folder(config)
    return {
        "addonsPath": str(addons) if addons else "",
        "source": source,
        "hasKey": bool(api_key(config)),
        "installed": list_installed(addons, load_state()),
    }


def command_search(args):
    config = load_config()
    key = require_key(config)
    query = args[0] if args else ""
    type_id = forever_type_id(config, key)
    params = {
        "gameId": WOW_GAME_ID,
        "gameVersionTypeId": type_id,
        "sortField": SORT_BY_POPULARITY,
        "sortOrder": "desc",
        "pageSize": PAGE_SIZE,
    }
    if query:
        params["searchFilter"] = query
    mods = api_get("/v1/mods/search", key, params).get("data") or []
    installed = load_state().get("mods") or {}
    return {
        "query": query,
        "results": [
            {
                "id": mod.get("id"),
                "name": mod.get("name", ""),
                "summary": mod.get("summary", ""),
                "downloads": mod.get("downloadCount", 0),
                "logo": (mod.get("logo") or {}).get("thumbnailUrl", ""),
                "url": (mod.get("links") or {}).get("websiteUrl", ""),
                "installed": str(mod.get("id")) in installed,
            }
            for mod in mods
        ],
    }


def command_install(args):
    if len(args) != 1 or not args[0].isdigit():
        raise AddonixError("usage", "install requires a numeric addon id.")
    mod_id = int(args[0])
    config = load_config()
    key = require_key(config)
    addons, _source = resolve_addons_folder(config)
    if not addons:
        raise AddonixError("addons_not_found", "Could not find the WoW Forever AddOns folder.")

    lock_path = state_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AddonixError("busy", "Another addon install is running.") from None
        return install_locked(mod_id, config, key, addons)


def install_locked(mod_id, config, key, addons):
    type_id = forever_type_id(config, key)
    mod = api_get(f"/v1/mods/{mod_id}", key).get("data") or {}
    website = (mod.get("links") or {}).get("websiteUrl", "")
    files = api_get(f"/v1/mods/{mod_id}/files", key, {"gameVersionTypeId": type_id, "pageSize": 50}).get("data") or []
    entry = choose_file(files)
    if not entry:
        raise AddonixError("no_compatible_file", "This addon has no file for WoW Forever yet.", website)
    url = file_download_url(mod_id, entry, key)
    if not url:
        raise AddonixError("distribution_disabled", "The author only allows downloads from the CurseForge site.", website)

    addons.mkdir(parents=True, exist_ok=True)
    # Staging sits beside AddOns so the final renames stay on one filesystem,
    # and outside it so the game never loads a half-finished folder.
    staging = Path(tempfile.mkdtemp(prefix=".addonix-", dir=addons.parent))
    try:
        archive_path = staging / "addon.zip"
        extract_root = staging / "extract"
        download(url, key, archive_path, entry)
        extract_root.mkdir()
        folders = extract_archive(archive_path, extract_root)
        swap_folders(folders, extract_root, addons, staging / "backup")
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    version = entry.get("displayName") or entry.get("fileName") or ""
    state = load_state()
    state.setdefault("mods", {})[str(mod_id)] = {"name": mod.get("name", ""), "version": version, "fileId": entry.get("id"), "folders": folders}
    write_private_json(state_path(), state)
    return {"modId": mod_id, "name": mod.get("name", ""), "version": version, "folders": folders}


def command_set_key(_args):
    # One line, not read(): the shell keeps stdin open after writing the secret.
    key = sys.stdin.readline().strip()
    if not key:
        raise AddonixError("missing_key", "The API key is empty.")
    fetch_forever_type_id(key)
    config = load_config()
    config["apiKey"] = key
    write_private_json(config_path(), config)
    return {}


COMMANDS = {
    "status": command_status,
    "search": command_search,
    "install": command_install,
    "set-key": command_set_key,
}


def main(argv):
    try:
        if not argv or argv[0] not in COMMANDS:
            raise AddonixError("usage", "Commands: " + ", ".join(COMMANDS))
        payload = {"ok": True, **COMMANDS[argv[0]](argv[1:])}
        code = 0
    except AddonixError as error:
        payload = {"ok": False, "code": error.code, "message": error.message}
        if error.url:
            payload["url"] = error.url
        code = 1
    except OSError as error:
        payload = {"ok": False, "code": "io", "message": f"File error: {error.strerror}"}
        code = 1
    except Exception as error:  # The panel only understands JSON, so nothing may escape as a traceback.
        payload = {"ok": False, "code": "internal", "message": f"Unexpected error: {type(error).__name__}"}
        code = 1
    print(json.dumps(payload))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
