#!/usr/bin/env python3
"""LLM must not claim/promise SCC actions, and must not poison voice-local history."""

from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from angus_operator.honesty import (  # noqa: E402
    UNVERIFIED_ACTION_REFUSE,
    claims_unverified_action,
    sanitize_llm_reply,
)
import glitch_llm  # noqa: E402


def _ollama_resp(text: str):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"message": {"role": "assistant", "content": text}}
    r.raise_for_status = lambda: None
    return r


CLAIMS = [
    "The porch lights will be turned off.",
    "I'll turn off the lights",
    "the door will be locked",
    "I locked the front door.",
    "The porch lights are now off.",
]
HARMLESS = [
    "A locked door is more secure.",
    "We can discuss how porch lights work.",
    "Frigate monitors cameras.",
    "I don't know what that meant.",
]


class PromiseSanitizerTests(unittest.TestCase):
    def test_future_tense_action_promises_are_blocked(self):
        for text in CLAIMS:
            with self.subTest(text=text):
                self.assertTrue(claims_unverified_action(text), text)
                self.assertEqual(sanitize_llm_reply(text), UNVERIFIED_ACTION_REFUSE)

    def test_harmless_discussion_still_allowed(self):
        for text in HARMLESS:
            with self.subTest(text=text):
                self.assertFalse(claims_unverified_action(text), text)
                self.assertEqual(sanitize_llm_reply(text), text)


class VoiceLocalHistoryTests(unittest.TestCase):
    def test_drops_claimed_assistant_turn_and_prior_user(self):
        poisoned = [
            {"role": "user", "content": "what about lunch"},
            {"role": "assistant", "content": "Maybe sandwiches."},
            {"role": "user", "content": "Turn off the forward slides."},
            {"role": "assistant", "content": "The porch lights will be turned off."},
        ]
        safe = glitch_llm._voice_safe_history(poisoned)
        blob = json.dumps(safe).lower()
        self.assertNotIn("porch lights will be turned off", blob)
        self.assertNotIn("forward slides", blob)
        self.assertEqual(safe[-1]["content"], "Maybe sandwiches.")

    def test_keeps_minimal_recent_non_action_turns(self):
        hist = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hey Ross."},
            {"role": "user", "content": "what time is it"},
            {"role": "assistant", "content": "It is morning."},
        ]
        with patch.object(glitch_llm, "voice_local_history_turns", return_value=2):
            safe = glitch_llm._voice_safe_history(hist)
        self.assertEqual(len(safe), 2)
        self.assertEqual(safe[0]["content"], "what time is it")


class ForwardSlidesRegressionTests(unittest.TestCase):
    def setUp(self):
        self.patches = [
            patch.object(glitch_llm, "_log_external", lambda *_a, **_k: None),
            patch.object(glitch_llm, "log_line", lambda *_a, **_k: None),
            patch.object(glitch_llm, "_try_hermes", side_effect=AssertionError("hermes")),
            patch.object(glitch_llm, "ask_xai", side_effect=AssertionError("xai")),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_claim_is_blocked_and_does_not_contaminate_next_request(self):
        captured = []

        def fake_post(url, **kwargs):
            captured.append(kwargs.get("json") or {})
            if len(captured) == 1:
                return _ollama_resp("The porch lights will be turned off.")
            return _ollama_resp("I don't follow. Can you say that again?")

        with patch("glitch_llm.requests.post", side_effect=fake_post):
            first = glitch_llm.ask_glitch("Turn off the forward slides.", voice=True, history=[])
            self.assertEqual(first["backend"], "local")
            self.assertTrue(first["sanitized"])
            self.assertEqual(first["reply"], UNVERIFIED_ACTION_REFUSE)
            self.assertNotIn("porch lights will", first["reply"].lower())

            poisoned = [
                {"role": "user", "content": "Turn off the forward slides."},
                {"role": "assistant", "content": "The porch lights will be turned off."},
            ]
            second = glitch_llm.ask_glitch("I don't know.", voice=True, history=poisoned)

        self.assertEqual(len(captured), 2)
        second_msgs = json.dumps(captured[1].get("messages") or []).lower()
        self.assertNotIn("porch lights will be turned off", second_msgs)
        self.assertNotIn("forward slides", second_msgs)
        self.assertIn("i don't know.", captured[1]["messages"][-1]["content"].lower())
        self.assertNotIn("porch lights will", second["reply"].lower())
        self.assertNotEqual(second["reply"], UNVERIFIED_ACTION_REFUSE)


if __name__ == "__main__":
    unittest.main()
