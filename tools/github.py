"""Restricted public GitHub lookup for the Kubernetes repository."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from serving.app.langfuse import observe
from agents.orchestrator.retry import DEFAULT_BACKOFF_SECONDS, retry_sync


API_BASE = "https://api.github.com"
REPOSITORY = "kubernetes/kubernetes"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_LIMIT = 20
MAX_RELEASE_BODY_CHARS = 2000
SUPPORTED_RESOURCE_TYPES = frozenset({"latest_release", "releases", "issues", "pull_requests", "tags", "changelog"})
RELEASE_TAG_PATTERN = re.compile(r"^v?(\d+)\.(\d+)(?:\.\d+)?$")
LOGGER = logging.getLogger(__name__)


def _failure(resource_type: str, code: str, message: str) -> dict[str, Any]:
    return {"resource_type": resource_type, "ok": False, "error": {"code": code, "message": message}}


def _headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "kubernetes-knowledge-assistant",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _is_retryable_request_error(error: Exception) -> bool:
    """Retry only transient GitHub failures; malformed or invalid requests fail fast."""
    if isinstance(error, HTTPError):
        remaining = error.headers.get("X-RateLimit-Remaining") if error.headers else None
        return (
            error.code in {408, 425, 429}
            or 500 <= error.code <= 599
            or (error.code == 403 and remaining == "0")
        )
    return isinstance(error, (URLError, TimeoutError, OSError))


def _log_retry(error: Exception, retry_number: int, delay: float) -> None:
    # The tool returns a structured error after its final failed attempt.
    LOGGER.warning(
        "GitHub request failed (%s); retry %d/%d in %s seconds",
        error,
        retry_number,
        len(DEFAULT_BACKOFF_SECONDS),
        delay,
    )


def _get_json(endpoint: str, params: Mapping[str, str | int] | None = None) -> tuple[Any | None, dict[str, Any] | None]:
    """Request an internally constructed GitHub endpoint and decode one JSON document."""
    url = f"{API_BASE}{endpoint}"
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers=_headers(), method="GET")
    try:
        response = retry_sync(
            lambda: urlopen(request, timeout=DEFAULT_TIMEOUT_SECONDS),  # nosec B310 - endpoint is fixed above
            should_retry=_is_retryable_request_error,
            on_retry=_log_retry,
        )
        with response:
            payload = response.read().decode("utf-8")
    except HTTPError as error:
        remaining = error.headers.get("X-RateLimit-Remaining") if error.headers else None
        if error.code == 429 or (error.code == 403 and remaining == "0"):
            return None, {"code": "rate_limited", "message": "GitHub API rate limit exceeded"}
        return None, {"code": "http_error", "message": f"GitHub API returned HTTP {error.code}"}
    except URLError as error:
        if isinstance(error.reason, TimeoutError):
            return None, {"code": "timeout", "message": "GitHub request timed out"}
        return None, {"code": "network_error", "message": f"GitHub request failed: {error.reason}"}
    except TimeoutError:
        return None, {"code": "timeout", "message": "GitHub request timed out"}
    except OSError as error:
        return None, {"code": "network_error", "message": f"GitHub request failed: {error}"}

    if not payload.strip():
        return None, {"code": "empty_response", "message": "GitHub returned an empty response"}
    try:
        return json.loads(payload), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, {"code": "malformed_response", "message": "GitHub returned invalid JSON"}


def _release(item: Mapping[str, Any]) -> dict[str, Any]:
    body = item.get("body")
    return {
        "name": item.get("name"),
        "tag_name": item.get("tag_name"),
        "published_at": item.get("published_at"),
        "html_url": item.get("html_url"),
        "prerelease": bool(item.get("prerelease")),
        "body": body[:MAX_RELEASE_BODY_CHARS] if isinstance(body, str) else None,
    }


def _work_item(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "updated_at": item.get("updated_at"),
        "html_url": item.get("html_url"),
    }


def _tag(item: Mapping[str, Any]) -> dict[str, Any]:
    commit = item.get("commit")
    return {
        "name": item.get("name"),
        "commit_sha": commit.get("sha") if isinstance(commit, Mapping) else None,
        "zipball_url": item.get("zipball_url"),
    }


def _search_query(query: str, kind: str) -> str:
    # Quote user text so it cannot add GitHub search qualifiers such as repo:other/project.
    literal_query = query.replace('"', " ").strip()
    return f'"{literal_query}" repo:{REPOSITORY} is:{kind}'


def _changelog_request(query: str) -> tuple[str, str, str] | None:
    """Derive one allow-listed changelog path from a Kubernetes release tag."""
    match = RELEASE_TAG_PATTERN.fullmatch(query.strip())
    if match is None:
        return None
    major, minor = match.groups()
    version = f"v{query.strip().lstrip('v')}"
    path = f"CHANGELOG/CHANGELOG-{major}.{minor}.md"
    return version, path, f"/repos/{REPOSITORY}/contents/{path}"


def _changelog_section(content: str, version: str) -> str:
    """Keep the requested release section when a series changelog includes multiple patches."""
    heading = re.compile(rf"^##\s+{re.escape(version)}\b.*$", re.MULTILINE)
    match = heading.search(content)
    if match is None:
        return content
    following = re.compile(r"^##\s+", re.MULTILINE).search(content, match.end())
    return content[match.start() : following.start() if following else None]


def _items(payload: Any) -> list[Mapping[str, Any]] | None:
    if not isinstance(payload, list) or not all(isinstance(item, Mapping) for item in payload):
        return None
    return list(payload)


@observe(name="github-kubernetes-lookup", as_type="tool")
def github_kubernetes_lookup(
    resource_type: str, query: str | None = None, limit: int = 5
) -> dict[str, Any]:
    """Look up a fixed, allow-listed Kubernetes GitHub resource."""
    if not isinstance(resource_type, str) or resource_type not in SUPPORTED_RESOURCE_TYPES:
        return _failure(str(resource_type), "unsupported_resource", "Unsupported GitHub resource type")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        return _failure(resource_type, "invalid_limit", f"limit must be an integer between 1 and {MAX_LIMIT}")
    if query is not None and (not isinstance(query, str) or not query.strip()):
        return _failure(resource_type, "invalid_query", "query must be a non-empty string when provided")
    query = query.strip() if query else None

    if resource_type == "latest_release":
        payload, error = _get_json(f"/repos/{REPOSITORY}/releases/latest")
        if error:
            return _failure(resource_type, error["code"], error["message"])
        if not isinstance(payload, Mapping):
            return _failure(resource_type, "malformed_response", "GitHub returned an invalid release response")
        if not payload:
            return _failure(resource_type, "empty_response", "GitHub returned no release data")
        return {"resource_type": resource_type, "ok": True, "release": _release(payload)}

    if resource_type == "changelog":
        if query is None:
            return _failure(resource_type, "invalid_query", "changelog requires a Kubernetes release tag such as v1.36.0")
        changelog = _changelog_request(query)
        if changelog is None:
            return _failure(resource_type, "invalid_query", "changelog query must be a Kubernetes release tag such as v1.36.0")
        version, path, endpoint = changelog
        payload, error = _get_json(endpoint, {"ref": "master"})
        if error:
            return _failure(resource_type, error["code"], error["message"])
        if not isinstance(payload, Mapping) or payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
            return _failure(resource_type, "malformed_response", "GitHub returned an invalid changelog response")
        try:
            encoded = "".join(payload["content"].split())
            decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            return _failure(resource_type, "malformed_response", "GitHub returned invalid changelog content")
        if not decoded.strip():
            return _failure(resource_type, "empty_response", "GitHub returned an empty changelog")
        section = _changelog_section(decoded, version)
        maximum = 12000
        return {
            "resource_type": resource_type,
            "ok": True,
            "version": version,
            "path": path,
            "html_url": payload.get("html_url"),
            "content": section[:maximum],
            "truncated": len(section) > maximum,
        }

    if resource_type == "releases":
        payload, error = _get_json(f"/repos/{REPOSITORY}/releases", {"per_page": limit})
        if error:
            return _failure(resource_type, error["code"], error["message"])
        items = _items(payload)
        if items is None:
            return _failure(resource_type, "malformed_response", "GitHub returned an invalid releases response")
        if query:
            needle = query.casefold()
            items = [
                item
                for item in items
                if needle in " ".join(str(item.get(field, "")) for field in ("name", "tag_name", "body")).casefold()
            ]
        return {"resource_type": resource_type, "ok": True, "releases": [_release(item) for item in items[:limit]]}

    if resource_type in {"issues", "pull_requests"} and query:
        kind = "issue" if resource_type == "issues" else "pr"
        payload, error = _get_json("/search/issues", {"q": _search_query(query, kind), "per_page": limit})
        if error:
            return _failure(resource_type, error["code"], error["message"])
        if not isinstance(payload, Mapping) or not isinstance(payload.get("items"), list):
            return _failure(resource_type, "malformed_response", "GitHub returned an invalid search response")
        items = payload["items"]
        if not all(isinstance(item, Mapping) for item in items):
            return _failure(resource_type, "malformed_response", "GitHub returned malformed search items")
        key = "issues" if resource_type == "issues" else "pull_requests"
        return {"resource_type": resource_type, "ok": True, key: [_work_item(item) for item in items[:limit]]}

    if resource_type == "issues":
        payload, error = _get_json(f"/repos/{REPOSITORY}/issues", {"state": "all", "per_page": limit})
        if error:
            return _failure(resource_type, error["code"], error["message"])
        items = _items(payload)
        if items is None:
            return _failure(resource_type, "malformed_response", "GitHub returned an invalid issues response")
        issues = [item for item in items if "pull_request" not in item]
        return {"resource_type": resource_type, "ok": True, "issues": [_work_item(item) for item in issues[:limit]]}

    if resource_type == "pull_requests":
        payload, error = _get_json(f"/repos/{REPOSITORY}/pulls", {"state": "all", "per_page": limit})
        if error:
            return _failure(resource_type, error["code"], error["message"])
        items = _items(payload)
        if items is None:
            return _failure(resource_type, "malformed_response", "GitHub returned an invalid pull requests response")
        return {"resource_type": resource_type, "ok": True, "pull_requests": [_work_item(item) for item in items[:limit]]}

    payload, error = _get_json(f"/repos/{REPOSITORY}/tags", {"per_page": limit})
    if error:
        return _failure(resource_type, error["code"], error["message"])
    items = _items(payload)
    if items is None:
        return _failure(resource_type, "malformed_response", "GitHub returned an invalid tags response")
    return {"resource_type": resource_type, "ok": True, "tags": [_tag(item) for item in items[:limit]]}
