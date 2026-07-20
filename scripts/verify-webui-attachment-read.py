#!/usr/bin/env python3
"""Isolated security and profile-boundary verifier for WebUI text attachments."""

from __future__ import annotations

import importlib.util
import json
import os
import tempfile
from pathlib import Path

import yaml


REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = REPO / "plugin" / "aota-tools" / "_webui_attachment_read.py"


def load_module():
    spec = importlib.util.spec_from_file_location("webui_attachment_read_fixture", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def result(module, **args):
    return json.loads(module.handle(args))


def rejected(module, expected: str, **args) -> None:
    payload = result(module, **args)
    assert payload.get("error") == expected, payload


def main() -> None:
    module = load_module()
    original_root = os.environ.get("AOTA_WEBUI_ATTACHMENT_ROOT")
    original_ref_root = os.environ.get("AOTA_WEBUI_ATTACHMENT_REF_ROOT")
    try:
        with tempfile.TemporaryDirectory(prefix="aota-webui-attachment-") as temp:
            root = Path(temp) / "attachments"
            session = root / "conversation-001"
            nested = session / "nested"
            nested.mkdir(parents=True)
            (session / "long-prompt.md").write_text("ATTACHMENT_SMOKE_NONCE=fixture\n" + "abcdef" * 10, encoding="utf-8")
            (session / "note.txt").write_text("plain text", encoding="utf-8")
            (session / "bom.md").write_bytes(b"\xef\xbb\xbfBOM text")
            (session / "empty.txt").write_text("", encoding="utf-8")
            (nested / "nested.md").write_text("nested", encoding="utf-8")
            (session / "document.pdf").write_bytes(b"%PDF-1.7")
            (session / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            (session / "document.docx").write_bytes(b"PK\x03\x04")
            (session / "binary.md").write_bytes(b"valid\x00binary")
            (session / "oversized.md").write_bytes(b"x" * (module.MAX_FILE_BYTES + 1))
            (session / "folder.md").mkdir()
            (session / "escape-link.md").symlink_to("/etc/passwd")
            parent_target = Path(temp) / "parent-target"
            parent_target.mkdir()
            (parent_target / "linked.md").write_text("outside", encoding="utf-8")
            (session / "parent-link").symlink_to(parent_target, target_is_directory=True)

            os.environ["AOTA_WEBUI_ATTACHMENT_ROOT"] = str(root)
            os.environ["AOTA_WEBUI_ATTACHMENT_REF_ROOT"] = "/home/hermeswebui/.hermes/webui/attachments"
            ref = "/home/hermeswebui/.hermes/webui/attachments/conversation-001/long-prompt.md"

            payload = result(module, attachment_ref=ref, max_chars=20, offset_chars=0)
            assert payload["content"] == "ATTACHMENT_SMOKE_NON", payload
            assert payload["truncated"] is True and payload["next_offset"] == 20, payload
            assert "attachment_root" not in payload and "path" not in payload, payload
            print("AOTA_WEBUI_ATTACHMENT_TEXT_READ_PASS")
            print("AOTA_WEBUI_ATTACHMENT_MD_PASS")

            txt = result(module, attachment_path="conversation-001/note.txt")
            assert txt["content"] == "plain text" and txt["extension"] == ".txt", txt
            bom = result(module, attachment_ref="conversation-001/bom.md")
            assert bom["content"] == "BOM text", bom
            empty = result(module, attachment_ref="conversation-001/empty.txt")
            assert empty["content"] == "" and empty["total_chars"] == 0, empty
            nested_payload = result(module, attachment_ref="conversation-001/nested/nested.md")
            assert nested_payload["content"] == "nested", nested_payload
            print("AOTA_WEBUI_ATTACHMENT_TXT_PASS")

            page_one = result(module, attachment_ref="conversation-001/long-prompt.md", max_chars=7, offset_chars=0)
            page_two = result(module, attachment_ref="conversation-001/long-prompt.md", max_chars=7, offset_chars=7)
            assert page_one["content"] + page_two["content"] == "ATTACHMENT_SMO" and page_one["next_offset"] == 7, (page_one, page_two)
            assert result(module, attachment_ref="conversation-001/long-prompt.md", max_chars=999999)["returned_chars"] <= module.HARD_MAX_CHARS
            print("AOTA_WEBUI_ATTACHMENT_PAGINATION_PASS")
            print("AOTA_WEBUI_ATTACHMENT_SIZE_BOUND_PASS")

            rejected(module, "ATTACHMENT_ROOT_REJECTED", attachment_ref="/etc/passwd")
            rejected(module, "ATTACHMENT_TRAVERSAL_REJECTED", attachment_ref="conversation-001/../note.txt")
            rejected(module, "ATTACHMENT_REFERENCE_INVALID", attachment_ref="conversation-001/%2e%2e/note.txt")
            rejected(module, "ATTACHMENT_REFERENCE_INVALID", attachment_ref="conversation-001//note.txt")
            rejected(module, "ATTACHMENT_NOT_FILE", attachment_ref="conversation-001/folder.md")
            rejected(module, "ATTACHMENT_EXTENSION_REJECTED", attachment_ref="conversation-001/document.pdf")
            rejected(module, "ATTACHMENT_EXTENSION_REJECTED", attachment_ref="conversation-001/image.png")
            rejected(module, "ATTACHMENT_EXTENSION_REJECTED", attachment_ref="conversation-001/document.docx")
            rejected(module, "ATTACHMENT_BINARY_REJECTED", attachment_ref="conversation-001/binary.md")
            rejected(module, "ATTACHMENT_TOO_LARGE", attachment_ref="conversation-001/oversized.md")
            rejected(module, "ATTACHMENT_SYMLINK_REJECTED", attachment_ref="conversation-001/escape-link.md")
            rejected(module, "ATTACHMENT_SYMLINK_REJECTED", attachment_ref="conversation-001/parent-link/linked.md")
            rejected(module, "ATTACHMENT_NOT_FOUND", attachment_ref="conversation-001/missing.md")
            rejected(module, "ATTACHMENT_ARGUMENT_INVALID", attachment_ref="conversation-001/note.txt", unexpected=True)
            assert "attachment_root" not in module.SCHEMA["parameters"]["properties"]
            assert not {"list", "search", "directory"}.intersection(module.SCHEMA["parameters"]["properties"])
            print("AOTA_WEBUI_ATTACHMENT_TRAVERSAL_REJECT_PASS")
            print("AOTA_WEBUI_ATTACHMENT_ROOT_ESCAPE_REJECT_PASS")
            print("AOTA_WEBUI_ATTACHMENT_SYMLINK_REJECT_PASS")
            print("AOTA_WEBUI_ATTACHMENT_BINARY_REJECT_PASS")
            print("AOTA_WEBUI_ATTACHMENT_EXTENSION_REJECT_PASS")

        task_main = yaml.safe_load((REPO / "profiles/task-main/config.yaml").read_text(encoding="utf-8"))
        assert "aota_webui_attachment_read" in task_main["toolsets"]
        for platform in ("api_server", "cli"):
            assert "aota_webui_attachment_read" in task_main["platform_toolsets"][platform]
        assert {"file", "terminal"}.issubset(task_main["agent"]["disabled_toolsets"])
        coder = yaml.safe_load((REPO / "profiles/coder/config.yaml").read_text(encoding="utf-8"))
        steward = yaml.safe_load((REPO / "profiles/project-steward/config.yaml").read_text(encoding="utf-8"))
        assert "terminal" in coder["agent"]["disabled_toolsets"]
        assert "aota_project_steward" in steward["toolsets"]
        print("AOTA_WEBUI_ATTACHMENT_TASK_MAIN_EXPOSURE_PASS")
        print("AOTA_WEBUI_ATTACHMENT_PROFILE_BOUNDARY_REGRESSION_PASS")
        print("AOTA_WEBUI_ATTACHMENT_ISOLATED_SMOKE_PASS")
    finally:
        if original_root is None:
            os.environ.pop("AOTA_WEBUI_ATTACHMENT_ROOT", None)
        else:
            os.environ["AOTA_WEBUI_ATTACHMENT_ROOT"] = original_root
        if original_ref_root is None:
            os.environ.pop("AOTA_WEBUI_ATTACHMENT_REF_ROOT", None)
        else:
            os.environ["AOTA_WEBUI_ATTACHMENT_REF_ROOT"] = original_ref_root


if __name__ == "__main__":
    main()
