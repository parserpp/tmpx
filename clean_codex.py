#!/usr/bin/env python3
"""Clean invalid codex auth files via CPA management API."""

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

API_URL = ""
HEADERS = {}


def init_config(url: str, key: str):
    global API_URL, HEADERS
    API_URL = url.rstrip("/")
    HEADERS = {
        "Accept": "application/json, text/plain, */*",
        "Authorization": f"Bearer {key}",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }


def get_auth_files():
    """Fetch all auth files from CPA."""
    resp = requests.get(f"{API_URL}/v0/management/auth-files", headers=HEADERS)
    resp.raise_for_status()
    return resp.json().get("files", [])


def check_quota(file_info: dict) -> dict:
    """Check quota for a single auth file, return result dict."""
    auth_index = file_info["auth_index"]
    account_id = file_info.get("id_token", {}).get("chatgpt_account_id", "")
    file_id = file_info["id"]

    payload = {
        "authIndex": auth_index,
        "method": "GET",
        "url": "https://chatgpt.com/backend-api/wham/usage",
        "header": {
            "Authorization": "Bearer $TOKEN$",
            "Content-Type": "application/json",
            "User-Agent": "codex_cli_rs/0.76.0 (Debian 13.0.0; x86_64) WindowsTerminal",
            "Chatgpt-Account-Id": account_id,
        },
    }
    hdrs = {**HEADERS, "Content-Type": "application/json"}
    try:
        resp = requests.post(
            f"{API_URL}/v0/management/api-call", headers=hdrs, json=payload, timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        status_code = data.get("status_code", -1)
        body = data.get("body", "")
        log.info("ID=%s  status_code=%s  body=%s", file_id, status_code, body)
        return {"id": file_id, "status_code": status_code, "body": body}
    except Exception as exc:
        log.error("ID=%s  error=%s", file_id, exc)
        return {"id": file_id, "status_code": -1, "body": str(exc)}


def disable_file(file_id: str) -> bool:
    """Disable an auth file by its id (name). Returns True on success."""
    hdrs = {**HEADERS, "Content-Type": "application/json"}
    payload = {"name": file_id, "disabled": True}
    try:
        resp = requests.patch(
            f"{API_URL}/v0/management/auth-files/status", headers=hdrs, json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            log.warning("Disable %s unexpected response: %s", file_id, data)
            return False
        log.info("Disabled: %s", file_id)
        return True
    except Exception as exc:
        log.error("Disable %s failed: %s", file_id, exc)
        return False


def delete_file(file_id: str) -> bool:
    """Delete an auth file by its id (name). Returns True on success."""
    try:
        resp = requests.delete(
            f"{API_URL}/v0/management/auth-files",
            headers=HEADERS,
            params={"name": file_id},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "ok":
            log.warning("Delete %s unexpected response: %s", file_id, data)
            return False
        log.info("Deleted: %s", file_id)
        return True
    except Exception as exc:
        log.error("Delete %s failed: %s", file_id, exc)
        return False


def cmd_check(args):
    """Default mode: check quota and disable 401 files."""
    files = get_auth_files()
    codex_files = [
        f for f in files
        if f.get("provider") == "codex" and not f.get("disabled")
    ]
    log.info("Found %d active codex auth files (skipped disabled)", len(codex_files))

    if not codex_files:
        return

    results = []
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = {pool.submit(check_quota, f): f for f in codex_files}
        for future in as_completed(futures):
            results.append(future.result())

    invalid = [r for r in results if r["status_code"] == 401]
    log.info("Found %d files with 401, disabling...", len(invalid))
    disabled_count = 0
    for r in invalid:
        if disable_file(r["id"]):
            disabled_count += 1

    log.info("Done. checked=%d, found_401=%d, disabled=%d", len(results), len(invalid), disabled_count)


def cmd_delete(args):
    """Delete mode: remove disabled codex files."""
    files = get_auth_files()
    targets = [
        f
        for f in files
        if f.get("provider") == "codex" and f.get("disabled") is True
    ]
    log.info("Found %d disabled codex files to delete", len(targets))

    deleted_count = 0
    for f in targets:
        if delete_file(f["id"]):
            deleted_count += 1

    log.info("Done. found=%d, deleted=%d", len(targets), deleted_count)


def main():
    parser = argparse.ArgumentParser(description="Clean invalid codex auth files")
    parser.add_argument("--url", required=True, help="CPA API URL (e.g. http://localahost:4001)")
    parser.add_argument("--key", required=True, help="CPA admin key")

    sub = parser.add_subparsers(dest="command")

    check_p = sub.add_parser("check", help="Check quota and disable 401 files (default)")
    check_p.add_argument(
        "-c", "--concurrency", type=int, default=20, help="Concurrent workers (default: 20)"
    )

    sub.add_parser("delete", help="Delete disabled & unavailable codex files")

    args = parser.parse_args()
    init_config(args.url, args.key)

    if args.command == "delete":
        cmd_delete(args)
    else:
        if not hasattr(args, "concurrency"):
            args.concurrency = 20
        cmd_check(args)


if __name__ == "__main__":
    main()
