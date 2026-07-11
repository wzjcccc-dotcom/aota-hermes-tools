"""aota_web_fetch — bounded HTTP(S) fetch with SSRF/DNS-rebinding protection.

Read-only narrow tool.  Uses only stdlib; no third-party dependencies.
"""

from __future__ import annotations

import html.parser
import http.client
import io
import json
import re
import socket
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ._web_safety import (
    MAX_REDIRECTS,
    WebFetchError,
    is_ip_global,
    validate_ip_global,
    validate_url,
)

TOOL_NAME = "aota_web_fetch"
TOOLSET_NAME = "aota_web_readonly"

SCHEMA = {
    "name": TOOL_NAME,
    "description": (
        "Use for fetching one known public HTTP/HTTPS URL. "
        "Prefer over delegate_task + terminal/curl for simple single-URL reads. "
        "Use delegate_task for multi-source research, search, browser workflows, "
        "or exploratory web investigation."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": "Public HTTP or HTTPS URL to fetch",
            },
            "mode": {
                "type": "string",
                "enum": ["readable_text", "raw_text", "json"],
                "description": "Output mode: readable_text (strip HTML tags), raw_text (raw body), json (JSON parse)",
                "default": "readable_text",
            },
            "max_bytes": {
                "type": "integer",
                "description": "Maximum bytes to read from response body (default 262144, max 1048576)",
                "default": 262144,
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Connection and read timeout in seconds (default 15, max 30)",
                "default": 15,
            },
        },
        "required": ["url"],
        "additionalProperties": False,
    },
}

# Fixed User-Agent, no model-supplied headers
_USER_AGENT = "AOTA-Web-Fetch/0.3"

# Content types we accept
_TEXT_TYPES = {"text/"}
_JSON_TYPES = {"application/json", "application/"}
_BINARY_CONTENT_PATTERNS = [
    "image/",
    "video/",
    "audio/",
    "application/octet-stream",
    "application/zip",
    "application/gzip",
    "application/x-tar",
    "application/x-bzip2",
    "application/x-7z-compressed",
    "application/x-rar-compressed",
    "application/pdf",
    "application/x-sharedlib",
    "application/x-executable",
    "application/x-dosexec",
    "application/vnd.",
]


# ---------------------------------------------------------------------------
# Custom HTTPConnection with peer-IP validation
# ---------------------------------------------------------------------------

class _ValidatedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that validates the peer IP is global after connect."""

    def connect(self) -> None:
        super().connect()
        try:
            peername = self.sock.getpeername()
        except OSError as e:
            raise WebFetchError(f"failed to get peer address: {e}") from e
        ip_str = peername[0]
        if not is_ip_global(ip_str):
            self.sock.close()
            self.sock = None
            raise WebFetchError(
                f"peer IP {ip_str} is not a global (public) address; connection rejected"
            )


class _ValidatedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that validates the peer IP is global after connect."""

    def connect(self) -> None:
        super().connect()
        try:
            peername = self.sock.getpeername()
        except OSError as e:
            raise WebFetchError(f"failed to get peer address: {e}") from e
        ip_str = peername[0]
        if not is_ip_global(ip_str):
            self.sock.close()
            self.sock = None
            raise WebFetchError(
                f"peer IP {ip_str} is not a global (public) address; connection rejected"
            )


class _ValidatedHTTPHandler(urllib.request.HTTPHandler):
    """HTTPHandler using _ValidatedHTTPConnection."""

    def http_open(self, req):
        return self.do_open(_ValidatedHTTPConnection, req)


class _ValidatedHTTPSHandler(urllib.request.HTTPSHandler):
    """HTTPSHandler using _ValidatedHTTPSConnection."""

    def https_open(self, req):
        return self.do_open(_ValidatedHTTPSConnection, req)


# ---------------------------------------------------------------------------
# Content-type helpers
# ---------------------------------------------------------------------------

def _is_text_content(content_type: str) -> bool:
    """Check if content type is text-like."""
    ct_lower = content_type.lower().strip()
    if ct_lower.startswith("text/"):
        return True
    if ct_lower.startswith("application/json"):
        return True
    if ct_lower.startswith("application/") and "+json" in ct_lower:
        return True
    if ct_lower.startswith("application/xml") or ct_lower.startswith("application/") and "+xml" in ct_lower:
        return True
    return False


def _is_binary_content(content_type: str) -> bool:
    """Heuristic binary check based on content-type."""
    ct_lower = content_type.lower().strip()
    for pat in _BINARY_CONTENT_PATTERNS:
        if ct_lower.startswith(pat):
            return True
    # application/* without known text sub-type
    if ct_lower.startswith("application/"):
        # known text-like application subtypes
        known_text = (
            "application/json",
            "application/xml",
        )
        if any(ct_lower.startswith(k) for k in known_text):
            return False
        # +json / +xml
        if "+json" in ct_lower or "+xml" in ct_lower:
            return False
        # Anything else application/* is suspicious but we allow for now
        return False
    return False


def _extract_charset(content_type: str) -> str:
    """Extract charset from Content-Type header, defaulting to utf-8."""
    charset = "utf-8"
    if "charset=" in content_type.lower():
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("charset="):
                charset = part.split("=", 1)[1].strip().strip("'\"").split(";")[0]
                break
    return charset


# ---------------------------------------------------------------------------
# HTML text extraction (stdlib only, no BeautifulSoup)
# ---------------------------------------------------------------------------

class _TextExtractor(html.parser.HTMLParser):
    """HTMLParser that extracts text content, stripping script/style tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._text_parts: list[str] = []
        self._skip_tag = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag_lower = tag.lower()
        if tag_lower in ("script", "style", "noscript", "head"):
            self._skip_tag = True
            self._skip_depth = 1
        elif self._skip_tag and tag_lower not in (
            "script", "style", "noscript", "head"
        ):
            # nested tag inside a skip region
            pass

    def handle_endtag(self, tag: str) -> None:
        if self._skip_tag:
            tag_lower = tag.lower()
            if tag_lower in ("script", "style", "noscript", "head"):
                self._skip_depth -= 1
                if self._skip_depth <= 0:
                    self._skip_tag = False
                    self._skip_depth = 0

    def handle_data(self, data: str) -> None:
        if not self._skip_tag:
            stripped = data.strip()
            if stripped:
                self._text_parts.append(stripped)

    def get_text(self) -> str:
        return " ".join(self._text_parts)


def _extract_readable_text(html_body: str) -> tuple[str, str | None]:
    """Extract readable text and title from HTML body."""
    # Extract title with regex
    title = None
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html_body, re.IGNORECASE | re.DOTALL)
    if title_match:
        title = title_match.group(1).strip()

    # Extract text with HTML parser
    extractor = _TextExtractor()
    try:
        extractor.feed(html_body)
    except html.parser.HTMLParseError:
        pass
    text = extractor.get_text()

    # Normalize whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text, title


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def handle(args: dict, **_kwargs) -> str:
    url: str = args.get("url", "")
    mode: str = args.get("mode", "readable_text")
    max_bytes: int = args.get("max_bytes", 262144)
    timeout_seconds: int = args.get("timeout_seconds", 15)

    # Clamp params
    if max_bytes < 1:
        max_bytes = 1
    max_bytes = min(max_bytes, 1048576)

    if timeout_seconds < 1:
        timeout_seconds = 1
    timeout_seconds = min(timeout_seconds, 30)

    if mode not in ("readable_text", "raw_text", "json"):
        mode = "readable_text"

    try:
        return _do_fetch(url, mode, max_bytes, timeout_seconds)
    except WebFetchError as e:
        return json.dumps({"error": str(e)}, sort_keys=True)
    except Exception as e:
        return json.dumps({"error": f"fetch failed: {e}"}, sort_keys=True)


def _do_fetch(
    url: str,
    mode: str,
    max_bytes: int,
    timeout_seconds: int,
) -> str:
    # Initial URL validation
    url = validate_url(url)
    parsed = urlparse(url)
    validate_ip_global(parsed.hostname)

    # Build opener with custom handlers (no proxy)
    proxy_handler = urllib.request.ProxyHandler({})
    http_handler = _ValidatedHTTPHandler()
    https_handler = _ValidatedHTTPSHandler()
    opener = urllib.request.build_opener(proxy_handler, http_handler, https_handler)

    # Track redirects
    redirect_count = 0
    current_url = url
    final_url = url
    status = 0
    content_type_header = ""
    body_bytes = b""
    truncated = False

    while redirect_count <= MAX_REDIRECTS:
        req = urllib.request.Request(
            current_url,
            data=None,
            headers={"User-Agent": _USER_AGENT},
            method="GET",
        )

        try:
            response = opener.open(req, timeout=timeout_seconds)
        except urllib.error.HTTPError as e:
            # If it's a redirect status, handle manually
            if e.code in (301, 302, 303, 307, 308):
                redirect_count += 1
                location = e.headers.get("Location")
                if not location:
                    return json.dumps(
                        {
                            "url": url,
                            "final_url": current_url,
                            "status": e.code,
                            "content_type": None,
                            "mode": mode,
                            "title": None,
                            "content": None,
                            "bytes_read": 0,
                            "truncated": False,
                            "redirect_count": redirect_count - 1,
                            "error": "redirect with no Location header",
                        },
                        sort_keys=True,
                    )

                # Re-validate redirected URL
                try:
                    # Handle relative redirects
                    from urllib.parse import urljoin

                    new_url = urljoin(current_url, location)
                    validate_url(new_url)
                    new_parsed = urlparse(new_url)
                    validate_ip_global(new_parsed.hostname)
                except WebFetchError as e:
                    return json.dumps(
                        {
                            "url": url,
                            "final_url": current_url,
                            "status": e.code,
                            "content_type": None,
                            "mode": mode,
                            "title": None,
                            "content": None,
                            "bytes_read": 0,
                            "truncated": False,
                            "redirect_count": redirect_count,
                            "error": f"redirect target rejected: {e}",
                        },
                        sort_keys=True,
                    )

                current_url = new_url
                continue
            else:
                # Non-redirect HTTP error
                status = e.code
                content_type_header = e.headers.get("Content-Type", "") if e.headers else ""
                final_url = current_url
                body_bytes = e.read(max_bytes)
                truncated = len(body_bytes) >= max_bytes
                break

        except urllib.error.URLError as e:
            return json.dumps(
                {
                    "url": url,
                    "final_url": current_url,
                    "status": None,
                    "content_type": None,
                    "mode": mode,
                    "title": None,
                    "content": None,
                    "bytes_read": 0,
                    "truncated": False,
                    "redirect_count": redirect_count,
                    "error": f"connection failed: {e.reason}",
                },
                sort_keys=True,
            )

        else:
            status = response.status
            content_type_header = response.headers.get("Content-Type", "")
            final_url = current_url

            # Check for redirect status codes
            if status in (301, 302, 303, 307, 308):
                redirect_count += 1
                location = response.headers.get("Location")
                if not location:
                    body_bytes = b""
                    truncated = False
                    break

                # Re-validate redirected URL
                try:
                    from urllib.parse import urljoin

                    new_url = urljoin(current_url, location)
                    validate_url(new_url)
                    new_parsed = urlparse(new_url)
                    validate_ip_global(new_parsed.hostname)
                except WebFetchError as e:
                    return json.dumps(
                        {
                            "url": url,
                            "final_url": current_url,
                            "status": status,
                            "content_type": content_type_header,
                            "mode": mode,
                            "title": None,
                            "content": None,
                            "bytes_read": 0,
                            "truncated": False,
                            "redirect_count": redirect_count - 1,
                            "error": f"redirect target rejected: {e}",
                        },
                        sort_keys=True,
                    )

                current_url = new_url
                response.close()
                continue

            # Read body with bounded streaming
            body_bytes = _read_bounded(response, max_bytes)
            truncated = len(body_bytes) >= max_bytes
            response.close()
            break

    else:
        # Exceeded max redirects
        return json.dumps(
            {
                "url": url,
                "final_url": current_url,
                "status": None,
                "content_type": None,
                "mode": mode,
                "title": None,
                "content": None,
                "bytes_read": 0,
                "truncated": False,
                "redirect_count": redirect_count,
                "error": f"exceeded maximum redirects ({MAX_REDIRECTS})",
            },
            sort_keys=True,
        )

    # Check content type for binary rejection
    if content_type_header:
        if _is_binary_content(content_type_header):
            return json.dumps(
                {
                    "url": url,
                    "final_url": final_url,
                    "status": status,
                    "content_type": content_type_header,
                    "mode": mode,
                    "title": None,
                    "content": None,
                    "bytes_read": 0,
                    "truncated": False,
                    "redirect_count": redirect_count,
                    "error": f"binary content type not accepted: {content_type_header.split(';')[0].strip()}",
                },
                sort_keys=True,
            )

    # Decode body
    charset = _extract_charset(content_type_header) if content_type_header else "utf-8"
    try:
        body_text = body_bytes.decode(charset, errors="replace")
    except (LookupError, ValueError):
        body_text = body_bytes.decode("utf-8", errors="replace")

    # Process according to mode
    if mode == "raw_text":
        result_content = body_text
        result_title = None
    elif mode == "json":
        ct_lower = content_type_header.lower() if content_type_header else ""
        is_json = (
            ct_lower.startswith("application/json")
            or "+json" in ct_lower
        )
        if not is_json and content_type_header:
            # Allow if no content-type header
            pass

        if truncated:
            result_content = "truncated_json_not_parsed"
            result_title = None
        else:
            try:
                parsed_json = json.loads(body_text)
                result_content = json.dumps(parsed_json, indent=2, sort_keys=True)
                result_title = None
            except json.JSONDecodeError as e:
                return json.dumps(
                    {
                        "url": url,
                        "final_url": final_url,
                        "status": status,
                        "content_type": content_type_header,
                        "mode": mode,
                        "title": None,
                        "content": None,
                        "bytes_read": len(body_bytes),
                        "truncated": truncated,
                        "redirect_count": redirect_count,
                        "error": f"JSON parse error: {e}",
                    },
                    sort_keys=True,
                )
    else:  # readable_text
        result_content, result_title = _extract_readable_text(body_text)

    return json.dumps(
        {
            "url": url,
            "final_url": final_url,
            "status": status,
            "content_type": content_type_header.split(";")[0].strip() if content_type_header else None,
            "mode": mode,
            "title": result_title,
            "content": result_content,
            "bytes_read": len(body_bytes),
            "truncated": truncated,
            "redirect_count": redirect_count,
            "error": None,
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def _read_bounded(response, max_bytes: int) -> bytes:
    """Read response body up to max_bytes."""
    chunks: list[bytes] = []
    total = 0
    while total < max_bytes:
        remaining = max_bytes - total
        chunk = response.read(min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)
