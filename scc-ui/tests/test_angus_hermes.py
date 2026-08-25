#!/usr/bin/env python3
"""Hermes client payload and safety tests (no live gateway, no deploy)."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("ANGUS_HERMES_ENABLED", "false")
os.environ.setdefault("ANGUS_HERMES_API_KEY", "")
os.environ.setdefault("ANGUS_HERMES_URL", "http://127.0.0.1:8642/v1")

import angus_hermes  # noqa: E402

HERMES_ENV = {
    "ANGUS_HERMES_ENABLED": "true",
    "ANGUS_HERMES_API_KEY": "test-key-xyz",
    "ANGUS_HERMES_URL": "http://127.0.0.1:8642/v1",
}


def _resp(payload, status=200):
    r = MagicMock()
    r.status_code = status
    r.headers = {}
    if isinstance(payload, dict):
        r.json.return_value = payload
        r.text = json.dumps(payload)
        r.content = r.text.encode()
    else:
        r.json.side_effect = ValueError("No JSON")
        r.text = str(payload)
        r.content = b""
    return r


def _ok_payload(text="HERMES LOCAL 64K OK", model="angus-hermes"):
    return {
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": 8, "completion_tokens": 3},
    }


class PayloadOverrideTests(unittest.TestCase):
    def setUp(self):
        angus_hermes.reset_circuit()

    def tearDown(self):
        angus_hermes.reset_circuit()

    def _complete(self, **env):
        merged = dict(HERMES_ENV)
        merged.update(env)
        with patch.dict(os.environ, merged), patch.object(
            requests, "post", return_value=_resp(_ok_payload())
        ) as post:
            result = angus_hermes.complete([{"role": "user", "content": "hi"}])
        return result, post

    def test_complete_sends_angus_hermes_model(self):
        result, post = self._complete()
        self.assertTrue(result["ok"])
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "angus-hermes")
        self.assertEqual(result["backend"], "hermes")

    def test_complete_does_not_send_provider_override(self):
        _, post = self._complete()
        body = post.call_args.kwargs["json"]
        self.assertNotIn("provider", body)
        self.assertNotEqual(body.get("provider"), "xai")
        dumped = json.dumps(body)
        self.assertNotIn('"provider"', dumped)
        self.assertNotIn("xai", dumped)

    def test_complete_does_not_hardcode_ollama_backend(self):
        _, post = self._complete()
        body = post.call_args.kwargs["json"]
        dumped = json.dumps(body)
        self.assertNotIn("ollama", dumped.lower())
        self.assertNotIn("11434", dumped)
        self.assertNotIn("custom", dumped.lower())
        url = post.call_args.args[0] if post.call_args.args else post.call_args.kwargs.get("url")
        self.assertTrue(str(url).startswith("http://127.0.0.1:8642/v1/chat/completions"))

    def test_complete_keeps_auth_and_loopback(self):
        _, post = self._complete()
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer test-key-xyz")
        self.assertFalse(post.call_args.kwargs.get("allow_redirects", True))

    def test_preview_has_no_xai_provider(self):
        with patch.dict(os.environ, HERMES_ENV):
            preview = angus_hermes.request_payload_preview([{"role": "user", "content": "hi"}])
        self.assertEqual(preview["model"], "angus-hermes")
        self.assertNotIn("provider", preview)
        self.assertNotEqual(preview.get("provider"), "xai")
        self.assertIsNone(preview["session_id"])
        self.assertIsNone(preview["user_identifiers"])
        self.assertNotIn("xai", json.dumps(preview))


class SafetyPreservedTests(unittest.TestCase):
    def setUp(self):
        angus_hermes.reset_circuit()

    def tearDown(self):
        angus_hermes.reset_circuit()

    def test_disabled_returns_none(self):
        with patch.dict(os.environ, {"ANGUS_HERMES_ENABLED": "false", "ANGUS_HERMES_API_KEY": "x"}):
            self.assertIsNone(angus_hermes.complete([{"role": "user", "content": "hi"}]))

    def test_complete_rejects_lan_without_http(self):
        with patch.dict(
            os.environ,
            {
                "ANGUS_HERMES_ENABLED": "true",
                "ANGUS_HERMES_API_KEY": "k",
                "ANGUS_HERMES_URL": "http://192.168.1.3:8642/v1",
            },
        ), patch.object(requests, "post") as post:
            result = angus_hermes.complete([{"role": "user", "content": "hi"}])
        post.assert_not_called()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "not_loopback")

    def test_circuit_opens_after_three_failures(self):
        with patch.dict(os.environ, HERMES_ENV), patch.object(
            requests, "post", side_effect=requests.exceptions.ConnectionError("refused")
        ) as post:
            for _ in range(3):
                r = angus_hermes.complete([{"role": "user", "content": "hi"}])
                self.assertEqual(r["error"], "connection_refused")
            self.assertEqual(post.call_count, 3)
            fourth = angus_hermes.complete([{"role": "user", "content": "hi"}])
            self.assertEqual(fourth["error"], "circuit_open")
            self.assertEqual(post.call_count, 3)

    def test_key_not_in_request_body(self):
        secret = "super-secret-hermes-key-xyz"
        captured = []

        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json"))
            return _resp(_ok_payload())

        with patch.dict(
            os.environ,
            {
                "ANGUS_HERMES_ENABLED": "true",
                "ANGUS_HERMES_API_KEY": secret,
                "ANGUS_HERMES_URL": "http://127.0.0.1:8642/v1",
            },
        ), patch.object(requests, "post", side_effect=fake_post):
            result = angus_hermes.complete([{"role": "user", "content": "hi"}])
        self.assertTrue(result["ok"])
        self.assertNotIn(secret, json.dumps(captured[0]))
        self.assertNotIn(secret, json.dumps(result))


if __name__ == "__main__":
    unittest.main()
