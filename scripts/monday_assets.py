#!/usr/bin/env python3
"""Preserve authorized Monday asset bytes under a strict preflight size budget.

Input metadata is produced by the connected Monday get_assets tool. Signed URLs
remain in that private input only; the persistent manifest never contains URLs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import certifi


def write_private(path, value):
    temp = path.with_suffix(".tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    os.replace(temp, path)


def asset_filename(asset):
    extension = re.sub(r"[^a-z0-9]", "", str(asset.get("file_extension") or "").lower())[:12]
    return str(asset["id"]) + ("." + extension if extension else ".bin")


def file_digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-bytes", type=int, default=1024 ** 3)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    root = Path(args.output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    lock = root / ".download.lock"
    lock_fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(lock_fd)
    manifest_path = root / "manifest.json"
    tls_context = ssl.create_default_context(cafile=certifi.where())
    try:
        prior = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        prior_rows = {r["asset_id"]: r for r in prior.get("assets", [])}
        metadata = json.loads(Path(args.metadata).read_text())["assets"]
        metadata.sort(key=lambda a: (a["priority"], int(a["id"])))
        records, chosen, budget = {}, [], args.max_bytes
        pinned = {}
        # A richer export may discover comment attachments after column files.
        # Retain already preserved bytes and count them against the same budget.
        for asset in metadata:
            path = root / asset_filename(asset)
            if path.exists() and path.stat().st_size == asset.get("file_size"):
                digest = file_digest(path)
                previous = prior_rows.get(str(asset["id"]), {})
                if previous.get("sha256") and previous["sha256"] != digest:
                    raise ValueError("Previously preserved asset digest changed")
                pinned[str(asset["id"])] = {"path": path.name, "sha256": digest, "size": path.stat().st_size}
                budget -= path.stat().st_size
        if budget < 0:
            raise ValueError("Existing preserved assets exceed the requested budget")
        for asset in metadata:
            asset_id = str(asset["id"])
            if not asset_id.isdigit():
                raise ValueError("Asset ID must be numeric")
            size = asset.get("file_size")
            record = {"asset_id": asset_id, "original_name": asset["name"], "declared_size": size,
                      "priority": asset["priority"], "references": asset.get("references", [])}
            records[asset_id] = record
            if asset_id in pinned:
                record.update(status="verified", **pinned[asset_id], verified_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
                continue
            if not isinstance(size, int) or size < 0:
                record.update(status="skipped", reason="Unknown declared size")
                continue
            if size > budget:
                record.update(status="skipped", reason="Aggregate preservation byte cap")
                continue
            budget -= size
            record["status"] = "pending"
            chosen.append(asset)

        def download(asset):
            asset_id = str(asset["id"])
            record = records[asset_id]
            filename = asset_filename(asset)
            path = root / filename
            previous = prior_rows.get(asset_id, {})
            if path.exists() and previous.get("status") == "verified" and path.stat().st_size == asset["file_size"]:
                digest = file_digest(path)
                if digest == previous.get("sha256"):
                    path.chmod(0o600)
                    return {**record, "status": "verified", "size": path.stat().st_size, "sha256": digest,
                            "path": filename, "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            temp = root / (filename + ".part")
            error = "Download failed"
            for attempt in range(2):
                digest, count = hashlib.sha256(), 0
                try:
                    request = Request(asset["public_url"], headers={"User-Agent": "MillCity-authorized-Monday-migration/1.0"})
                    with urlopen(request, timeout=90, context=tls_context) as response:
                        declared = response.headers.get("Content-Length")
                        if declared and int(declared) > asset["file_size"]:
                            raise ValueError("Response exceeds prechecked size")
                        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                        with os.fdopen(fd, "wb") as handle:
                            while True:
                                block = response.read(1024 * 1024)
                                if not block:
                                    break
                                count += len(block)
                                if count > asset["file_size"]:
                                    raise ValueError("Download exceeds prechecked size")
                                handle.write(block)
                                digest.update(block)
                    if count != asset["file_size"]:
                        raise ValueError("Downloaded size differs from metadata")
                    os.replace(temp, path)
                    path.chmod(0o600)
                    return {**record, "status": "verified", "size": count, "sha256": digest.hexdigest(),
                            "path": filename, "verified_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
                    # Exception text can contain a signed URL: persist only its type/status.
                    error = f"{type(exc).__name__}" + (f" HTTP {exc.code}" if isinstance(exc, HTTPError) else "")
                    temp.unlink(missing_ok=True)
                    if attempt == 0:
                        time.sleep(1)
            return {**record, "status": "failed", "reason": error}

        def manifest():
            rows = list(records.values())
            verified = [row for row in rows if row["status"] == "verified"]
            return {"budget_bytes": args.max_bytes, "prechecked_bytes": args.max_bytes - budget,
                    "metadata_asset_count": len(metadata), "verified_count": len(verified),
                    "verified_bytes": sum(row["size"] for row in verified),
                    "skipped_count": sum(row["status"] == "skipped" for row in rows),
                    "failed_count": sum(row["status"] == "failed" for row in rows), "assets": rows}

        write_private(manifest_path, manifest())
        print(json.dumps({"selected_assets": len(chosen) + len(pinned), "new_downloads": len(chosen),
                          "already_verified": len(pinned), "selected_bytes": args.max_bytes - budget,
                          "skipped_assets": len(metadata) - len(chosen) - len(pinned), "budget_bytes": args.max_bytes}), flush=True)
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 8))) as pool:
            futures = {pool.submit(download, asset): asset for asset in chosen}
            for future in as_completed(futures):
                result = future.result()
                records[result["asset_id"]] = result
                current = manifest()
                write_private(manifest_path, current)
                if (current["verified_count"] > 0 and current["verified_count"] % 20 == 0) or (current["failed_count"] and current["failed_count"] % 20 == 0):
                    print(json.dumps({key: current[key] for key in ("verified_count", "verified_bytes", "failed_count")}), flush=True)
        final = manifest()
        write_private(manifest_path, final)
        print(json.dumps({key: value for key, value in final.items() if key != "assets"}), flush=True)
        return 2 if final["failed_count"] else 0
    finally:
        lock.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
