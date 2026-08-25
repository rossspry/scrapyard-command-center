#!/usr/bin/env python3
"""Voice backend selection: local qwen3:1.7b first, xAI fallback, Hermes for non-voice."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import glitch_llm  # noqa: E402


def _ollama_resp(text="Tea sounds good."):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"message": {"role": "assistant", "content": text}}
    r.raise_for_status = lambda: None
    return r


class VoiceLocalPathTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(glitch_llm, "_log_external", lambda *_a, **_k: None),
            patch.object(glitch_llm, "_sanitize_reply", lambda text: ((text or "").strip(), False)),
            patch.object(glitch_llm, "log_line", lambda *_a, **_k: None),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_voice_posts_qwen17_with_thinking_disabled(self):
        with patch.object(glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")) as hermes, patch.object(
            glitch_llm, "ask_xai", side_effect=AssertionError("xai")
        ), patch("glitch_llm.requests.post", return_value=_ollama_resp()) as post:
            result = glitch_llm.ask_glitch("what about lunch", voice=True)
        hermes.assert_not_called()
        self.assertEqual(result["backend"], "local")
        self.assertEqual(result["model"], "qwen3:1.7b")
        self.assertEqual(result["reply"], "Tea sounds good.")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["model"], "qwen3:1.7b")
        self.assertIn("think", body)
        self.assertFalse(body["think"])
        url = post.call_args.args[0]
        self.assertIn("/api/chat", url)
        self.assertNotIn("8642", url)
        self.assertNotIn("xai", url)
        self.assertEqual(post.call_args.kwargs["timeout"], 12.0)

    def test_voice_model_env_override(self):
        with patch.dict(os.environ, {"GLITCH_VOICE_LOCAL_MODEL": "qwen3:1.7b-fast"}), patch(
            "glitch_llm.requests.post", return_value=_ollama_resp("ok")
        ) as post, patch.object(glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")):
            result = glitch_llm.ask_glitch("hi", voice=True)
        self.assertEqual(result["model"], "qwen3:1.7b-fast")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "qwen3:1.7b-fast")
        self.assertFalse(post.call_args.kwargs["json"]["think"])

    def test_voice_does_not_call_hermes_even_when_enabled(self):
        with patch.object(glitch_llm, "_hermes_mode", return_value=True), patch.object(
            glitch_llm, "_try_hermes", side_effect=AssertionError("hermes used for voice")
        ), patch.object(glitch_llm, "ask_ollama", return_value="local hello"):
            result = glitch_llm.ask_glitch("hello", voice=True)
        self.assertEqual(result["backend"], "local")
        self.assertEqual(result["reply"], "local hello")

    def test_nonvoice_still_uses_hermes_when_enabled(self):
        hermes_hit = {"n": 0}

        def fake_hermes(*_a, **_k):
            hermes_hit["n"] += 1
            return {
                "ok": True,
                "reply": "deep memory answer",
                "backend": "hermes",
                "model": "angus-hermes",
                "sanitized": False,
            }

        with patch.object(glitch_llm, "_hermes_mode", return_value=True), patch.object(
            glitch_llm, "_try_hermes", side_effect=fake_hermes
        ), patch.object(glitch_llm, "ask_ollama", side_effect=AssertionError("voice model")):
            result = glitch_llm.ask_glitch("remember this", voice=False)
        self.assertEqual(hermes_hit["n"], 1)
        self.assertEqual(result["backend"], "hermes")
        self.assertEqual(result["model"], "angus-hermes")

    def test_voice_falls_back_to_xai_when_local_fails(self):
        with patch.object(
            glitch_llm, "ask_ollama", side_effect=RuntimeError("ollama down")
        ), patch.object(glitch_llm, "xai_available", return_value=True), patch.object(
            glitch_llm, "ask_xai", return_value="from xai"
        ) as xai, patch.object(
            glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")
        ):
            result = glitch_llm.ask_glitch("hello", voice=True)
        xai.assert_called()
        self.assertEqual(result["backend"], "xai")
        self.assertEqual(result["reply"], "from xai")

    def test_voice_unavailable_when_local_and_xai_fail(self):
        with patch.object(
            glitch_llm, "ask_ollama", side_effect=RuntimeError("ollama down")
        ), patch.object(glitch_llm, "xai_available", return_value=True), patch.object(
            glitch_llm, "ask_xai", side_effect=RuntimeError("xai down")
        ), patch.object(glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")):
            result = glitch_llm.ask_glitch("hello", voice=True)
        self.assertEqual(result["backend"], "failure")
        self.assertIn("trouble reaching", result["reply"].lower())

    def test_iter_voice_chunks_sanitized_reply_without_hermes(self):
        with patch.object(glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")), patch.object(
            glitch_llm, "ask_ollama", return_value="First sentence. Second sentence."
        ), patch.object(glitch_llm, "ask_xai", side_effect=AssertionError("xai")):
            meta = {}
            yielded = list(
                glitch_llm.iter_glitch_sentences("hello", voice=True, meta=meta, history=[])
            )
        self.assertEqual(meta["backend"], "local")
        self.assertEqual(meta["model"], "qwen3:1.7b")
        self.assertTrue(yielded)
        self.assertEqual(" ".join(yielded), "First sentence. Second sentence.")

    def test_voice_local_uses_short_timeout(self):
        with patch.dict(os.environ, {"GLITCH_VOICE_LOCAL_TIMEOUT": "8"}), patch(
            "glitch_llm.requests.post", return_value=_ollama_resp("ok")
        ) as post, patch.object(glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")), patch.object(
            glitch_llm, "ask_xai", side_effect=AssertionError("xai")
        ):
            result = glitch_llm.ask_glitch("hi", voice=True)
        self.assertEqual(result["backend"], "local")
        self.assertEqual(post.call_args.kwargs["timeout"], 8.0)

    def test_voice_timeout_falls_back_to_xai(self):
        with patch(
            "glitch_llm.requests.post",
            side_effect=requests.exceptions.Timeout("voice-local stalled"),
        ), patch.object(glitch_llm, "xai_available", return_value=True), patch.object(
            glitch_llm, "ask_xai", return_value="from xai after timeout"
        ) as xai, patch.object(
            glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")
        ):
            result = glitch_llm.ask_glitch("hello", voice=True)
        xai.assert_called()
        self.assertEqual(result["backend"], "xai")
        self.assertEqual(result["reply"], "from xai after timeout")

    def test_general_ollama_timeout_stays_180(self):
        with patch("glitch_llm.requests.post", return_value=_ollama_resp("ok")) as post:
            glitch_llm.ask_ollama("hello", voice=False)
        self.assertEqual(post.call_args.kwargs["timeout"], 180.0)

        with patch.object(glitch_llm, "_hermes_mode", return_value=False), patch.object(
            glitch_llm, "choose_backend", return_value=("local", "hello")
        ), patch("glitch_llm.requests.post", return_value=_ollama_resp("ok")) as post2:
            result = glitch_llm.ask_glitch("hello", voice=False)
        self.assertEqual(result["backend"], "local")
        self.assertEqual(post2.call_args.kwargs["timeout"], 180.0)
        self.assertNotEqual(post2.call_args.kwargs["timeout"], glitch_llm.voice_local_timeout_s())

    def test_voice_local_payload_helper_think_false(self):
        payload = glitch_llm._ollama_payload(
            "hi",
            None,
            voice=True,
            max_tokens=80,
            stream=False,
            model="qwen3:1.7b",
            think=False,
        )
        self.assertEqual(payload["model"], "qwen3:1.7b")
        self.assertFalse(payload["think"])
        self.assertNotEqual(payload["model"], "angus-local")
        self.assertNotEqual(payload["model"], "angus-hermes")


if __name__ == "__main__":
    unittest.main()
