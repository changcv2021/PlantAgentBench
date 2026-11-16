"""Bounded official metadata acquisition and immutable file inventories."""

import hashlib
import json
import re
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .common import clean_url, safe_relative, sha256, utc_now, write_json


class HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Refusing a non-HTTPS redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class MetadataClient:
    def __init__(self, directory, config):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.config = config
        self.evidence = []

    def fetch(self, url, name):
        if urlsplit(url).scheme != "https":
            raise ValueError("Only HTTPS metadata endpoints are allowed")
        safe_relative(name)
        event = {"requested_url": clean_url(url), "accessed_at_utc": utc_now(), "http_status": None}
        body = b""
        try:
            request = Request(url, headers={"User-Agent": "PlantAgentBench-metadata/0.1", "Accept-Encoding": "identity"})
            try:
                response = build_opener(HTTPSRedirects()).open(request, timeout=self.config["timeout_seconds"])
            except HTTPError as exc:
                response = exc
            with response:
                body = response.read(self.config["metadata_limit_bytes"] + 1)
                event.update(http_status=response.status, final_url=clean_url(response.url),
                             content_type=response.headers.get("Content-Type"),
                             retry_after=response.headers.get("Retry-After"))
            if len(body) > self.config["metadata_limit_bytes"]:
                event["error"] = "metadata_size_limit_exceeded"
                body = b""
        except (OSError, URLError, ValueError) as exc:
            event["error_type"] = type(exc).__name__
        path = self.directory / name
        path.write_bytes(body)
        event.update(body_ref=str(path), bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
        write_json(path.with_suffix(path.suffix + ".http.json"), event)
        self.evidence.append(event)
        if event["http_status"] != 200 or "error" in event or "error_type" in event:
            raise ValueError(f"metadata_request_failed: status={event['http_status']}")
        return body

    def json(self, url, name):
        return json.loads(self.fetch(url, name))


def discover_source(dataset_id, source, data_root, config):
    snapshot = utc_now().replace(":", "").replace("+", "_") + "-" + uuid.uuid4().hex[:8]
    client = MetadataClient(Path(data_root) / "metadata" / dataset_id / snapshot, config)
    result = {"dataset_id": dataset_id, "provider": source["provider"], "accessed_at_utc": utc_now(),
              "acquisition_scope": "metadata", "acquisition_completion": "not_started",
              "download_access_status": "unresolved", "release_id": None, "full_revision_sha": None,
              "concept_doi": None, "version_doi": None, "title": None, "creators": None,
              "license_declared": None, "missing_reason": {}, "version_candidates": [],
              "official_entry_evidence": source, "selection_reason": "Fixed source registry; no mirror or third-party substitutions"}
    files = []
    try:
        if source["provider"] == "zenodo":
            data = client.json(f"https://zenodo.org/api/records/{source['candidate_record_id']}", "record.json")
            metadata = data["metadata"]
            resolved = str(data["id"])
            result.update(title=metadata["title"], creators=metadata.get("creators"), release_id=resolved,
                          resolved_repository=f"https://zenodo.org/records/{resolved}",
                          concept_doi=data.get("conceptdoi"), version_doi=data.get("doi") or metadata.get("doi"),
                          license_declared=metadata.get("license", {}).get("id"),
                          download_access_status=metadata.get("access_right", "unresolved"))
            result["version_candidates"] = [{"requested_record": source["candidate_record_id"], "resolved_record": resolved}]
            if resolved != source["candidate_record_id"]:
                result["selection_reason"] = "Candidate API resolves to this concrete record; actual version DOI and checksums preserved"
            for f in data["files"]:
                name = f["key"]
                safe_relative(name)
                url = f["links"]["self"]
                if urlsplit(url).hostname != "zenodo.org":
                    raise ValueError("Unexpected Zenodo file host")
                algorithm, digest = f["checksum"].split(":", 1)
                files.append({"path": name, "size_bytes": f["size"], "download_url": url,
                              "source_checksum": {"algorithm": algorithm, "value": digest}})
        elif source["provider"] == "huggingface":
            repo = source["repo_id"]
            api = f"https://huggingface.co/api/datasets/{repo}"
            candidate = client.json(api, "candidate.json")
            revision = candidate["sha"]
            if not re.fullmatch(r"[0-9a-f]{40}", revision):
                raise ValueError("Expected full repository revision")
            data = client.json(f"{api}/revision/{revision}?blobs=true", "revision.json")
            if data["sha"] != revision or data["id"] != repo:
                raise ValueError("Repository identity or revision mismatch")
            card = data.get("cardData", {})
            result.update(title=card.get("pretty_name", repo), release_id=revision, full_revision_sha=revision,
                          resolved_repository=f"https://huggingface.co/datasets/{repo}", repo_id=repo,
                          license_declared=card.get("license"),
                          download_access_status="gated" if data.get("gated") else "open")
            for f in data["siblings"]:
                name = f["rfilename"]
                safe_relative(name)
                checksum = ({"algorithm": "sha256", "value": f["lfs"]["sha256"]} if f.get("lfs")
                            else {"algorithm": "git_blob_sha1", "value": f["blobId"]})
                files.append({"path": name, "size_bytes": f["size"], "source_checksum": checksum,
                              "download_url": f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{quote(name, safe='/')}"})
            if any(f["path"] == "README.md" for f in files):
                client.fetch(f"https://huggingface.co/datasets/{repo}/raw/{revision}/README.md", "README.md")
        else:
            client.fetch(source["landing_url"], "landing.html")
            # DOI's public DataCite record is the official migration evidence.
            metadata = client.json("https://api.datacite.org/dois/" + source["candidate_doi"], "datacite.json")["data"]["attributes"]
            target = metadata["url"]
            if urlsplit(target).hostname != "www.research-collection.ethz.ch":
                raise ValueError("Unexpected ETH repository identity")
            result.update(title=metadata["titles"][0]["title"], creators=metadata.get("creators"),
                          concept_doi=source["candidate_doi"], resolved_repository=target,
                          repository_metadata_updated=metadata.get("updated"), rights_metadata=metadata.get("rightsList", []))
            try:
                page = client.fetch(target, "repository.html").decode("utf-8")
            except ValueError:
                # A broken repository UI does not invalidate independently
                # accessible official item/bundle APIs. Keep its HTTP evidence.
                page = ""
                result["metadata_warnings"] = ["Repository HTML unavailable; authoritative API checked separately"]
            match = re.search(r'<script[^>]*id="dspace-angular-state"[^>]*>(.*?)</script>', page, re.S)
            candidates = []
            if match:
                payload = match[1]
                for old, new in [("&q;", '"'), ("&s;", "'"), ("&l;", "<"), ("&g;", ">"), ("&a;", "&")]:
                    payload = payload.replace(old, new)
                state = json.loads(payload)
                def walk(value):
                    if isinstance(value, dict):
                        yield value
                        for nested in value.values():
                            yield from walk(nested)
                    elif isinstance(value, list):
                        for nested in value:
                            yield from walk(nested)
                seen = set()
                for item in walk(state):
                    if item.get("bundleName") == "ORIGINAL" and "sizeBytes" in item and item.get("uuid") not in seen:
                        seen.add(item["uuid"])
                        candidates.append({"path": item.get("_name"), "size_bytes": item["sizeBytes"], "bitstream_uuid": item["uuid"],
                                           "download_url": item.get("_links", {}).get("content", {}).get("href"), "source_checksum": None})
                candidate_path = client.directory / "partial_original_file_candidates.json"
                write_json(candidate_path, {"inventory_complete": False, "reason": "SSR contains only a paginated subset; attachment API has not succeeded", "files": candidates})
                result["partial_file_candidates_ref"] = str(candidate_path)
            result["download_access_status"] = "official_repository_confirmed_attachments_unresolved"
            result["blocking_issues"] = ["BLOCKED_REPOSITORY_FILE_INVENTORY", "BLOCKED_LICENSE"]
            # Confirmed migration item and ORIGINAL bundle, not an unofficial mirror.
            item = client.json("https://www.research-collection.ethz.ch/server/api/core/items/5b9655e3-ce8b-49e8-91de-7821c91a371d", "item.json")
            if item.get("uuid") != "5b9655e3-ce8b-49e8-91de-7821c91a371d" or item.get("metadata", {}).get("dc.title", [{}])[0].get("value") != result["title"]:
                raise ValueError("ETH item does not match the DOI identity")
            page = client.json("https://www.research-collection.ethz.ch/server/api/core/bundles/9259c6a5-cb79-462d-9258-d097ef991f73/bitstreams?size=100", "originals.json")
            originals = page.get("_embedded", {}).get("bitstreams", [])
            if page.get("page", {}).get("totalPages") != 1 or page["page"]["totalElements"] != len(originals):
                raise ValueError("ETH ORIGINAL file pagination is incomplete")
            for original in originals:
                if original.get("bundleName") != "ORIGINAL":
                    raise ValueError("Unexpected ETH bitstream bundle")
                name, url = original["name"], original["_links"]["content"]["href"]
                safe_relative(name)
                checksum = original["checkSum"]
                if urlsplit(url).hostname != "www.research-collection.ethz.ch" or checksum["checkSumAlgorithm"] != "MD5" or not re.fullmatch("[0-9a-f]{32}", checksum["value"]):
                    raise ValueError("Unverified ETH file host or checksum")
                files.append({"path": name, "size_bytes": original["sizeBytes"], "download_url": url,
                              "source_checksum": {"algorithm": "md5", "value": checksum["value"]}})
            identity = hashlib.sha256(json.dumps(sorted(files, key=lambda f:f["path"]), sort_keys=True).encode()).hexdigest()
            license_name = item.get("metadata", {}).get("dc.rights.license", [{}])[0].get("value")
            result.update(release_id="eth-snapshot-" + identity[:16], source_snapshot_content_sha256=identity,
                          version_basis="Locally named immutable inventory/checksum snapshot, not an asserted author version tag",
                          license_declared=license_name, license_uri=item.get("metadata", {}).get("dc.rights.uri", [{}])[0].get("value"),
                          download_access_status="open", repository_item_uuid=item["uuid"], repository_last_modified=item.get("lastModified"),
                          blocking_issues=[])
            pdf = next((f for f in files if f["path"] == "README.pdf"), None)
            if pdf:
                try:
                    client.fetch(pdf["download_url"], "README.pdf")
                except ValueError:
                    result.setdefault("metadata_warnings", []).append("README content pending acquisition; complete authoritative file manifest remains retained")
    except (ValueError, KeyError, TypeError) as exc:
        result["discovery_error_type"] = type(exc).__name__
        result["discovery_status"] = "blocked"
        result.setdefault("blocking_issues", []).append("BLOCKED_SOURCE_METADATA")
    else:
        result["discovery_status"] = "complete" if files else "blocked"
    if len({f["path"] for f in files}) != len(files):
        raise ValueError("Duplicate remote file paths")
    inventory = client.directory / "files.json"
    write_json(inventory, sorted(files, key=lambda f: f["path"]))
    result.update(file_inventory_ref=str(inventory), file_inventory_sha256=sha256(inventory),
                  metadata_snapshot_refs=client.evidence, file_count=len(files),
                  raw_bytes=sum(f["size_bytes"] for f in files) if files else None)
    result["source_license_evidence_refs"] = [x["body_ref"] for x in client.evidence
                                             if x["http_status"] == 200 and Path(x["body_ref"]).name in {"record.json", "revision.json", "README.md", "datacite.json", "repository.html", "item.json", "README.pdf"}]
    for field in ["title", "creators", "concept_doi", "version_doi", "release_id", "full_revision_sha", "license_declared"]:
        if result[field] is None:
            result["missing_reason"][field] = "Not exposed by saved metadata or request unresolved; not inferred"
    write_json(client.directory / "discovery.json", result)
    return result
