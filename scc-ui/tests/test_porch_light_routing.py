#!/usr/bin/env python3
"""Porch-light commands must hit HA before honesty.yard; malformed STT must not."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from angus_light_intent import expand_stt_command, porch_light_action  # noqa: E402
from angus_operator.honesty import block_unmatched_yard  # noqa: E402
from angus_operator.tools.ha import match_porch  # noqa: E402
import glitch_voice_hybrid as voice  # noqa: E402


OFF_VARIANTS = [
    "turn off scrapyard porch light",
    "Turn off scrap yard porch light.",
    "Turnoff, scrap yard porch light.",
    "turnoff scrap yard porch light",
    "turn off the scrapyard porch light",
    "turn off scrapyard porch lights",
    "turn off the scrapyard porch lights",
    "turn off scrap yard porch lights",
]

ON_VARIANTS = [
    "turn on scrapyard porch light",
    "turn on the scrapyard porch lights",
    "Turnon, scrap yard porch light.",
]

NOT_A_COMMAND = [
    "Turn off scrapyard port line.",
    "turn off scrapyard port line",
    "what about the porch",
    "the porch light looks nice",
]


class ExpandAndMatchTests(unittest.TestCase):
    def test_turnoff_scrap_yard_expands(self):
        self.assertEqual(
            expand_stt_command("Turnoff, scrap yard porch light."),
            "turn off scrapyard porch light",
        )

    def test_off_variants_are_porch_off(self):
        for text in OFF_VARIANTS:
            with self.subTest(text=text):
                self.assertEqual(porch_light_action(text), "off", text)
                self.assertTrue(voice.is_porch_light_off_request(text), text)
                self.assertFalse(voice.is_porch_light_on_request(text), text)
                matched = match_porch(text)
                self.assertIsNotNone(matched, text)
                self.assertEqual(matched["action"], "off")
                self.assertEqual(matched["entity_id"], "switch.scrapyard_porch_light")

    def test_on_variants_are_porch_on(self):
        for text in ON_VARIANTS:
            with self.subTest(text=text):
                self.assertEqual(porch_light_action(text), "on", text)
                self.assertTrue(voice.is_porch_light_on_request(text), text)

    def test_port_line_is_not_a_porch_command(self):
        for text in NOT_A_COMMAND:
            with self.subTest(text=text):
                self.assertIsNone(porch_light_action(text), text)
                self.assertFalse(voice.is_porch_light_off_request(text), text)
                self.assertFalse(voice.is_porch_light_on_request(text), text)
                self.assertIsNone(match_porch(text), text)


class RoutingOrderTests(unittest.TestCase):
    def test_local_handler_runs_before_honesty_for_turnoff(self):
        text = "Turnoff, scrap yard porch light."
        self.assertTrue(voice.is_porch_light_off_request(text))
        self.assertTrue(block_unmatched_yard(text))
        with patch.object(voice, "HA_TOKEN", "test-token"), patch.object(
            voice, "speak"
        ) as speak, patch.object(voice, "remember_thread"), patch(
            "glitch_voice_hybrid.requests.post", return_value=MagicMock()
        ) as post:
            post.return_value.raise_for_status = lambda: None
            timing = {}
            handled = voice.handle_local_device_command(text, timing)
        self.assertTrue(handled)
        self.assertEqual(timing.get("route_via"), "local_ha_porch")
        post.assert_called()
        url = post.call_args.args[0]
        self.assertIn("/switch/turn_off", url)
        self.assertEqual(
            post.call_args.kwargs["json"]["entity_id"], "switch.scrapyard_porch_light"
        )
        speak.assert_called()
        spoken = " ".join(str(c.args[0]) for c in speak.call_args_list if c.args)
        self.assertIn("turning off the scrapyard porch lights", spoken.lower())
        self.assertNotIn("didn't run a yard tool", spoken.lower())

    def test_ha_failure_does_not_claim_success(self):
        text = "turn off scrapyard porch light"
        err = MagicMock()
        err.raise_for_status.side_effect = Exception("ha down")
        with patch.object(voice, "HA_TOKEN", "test-token"), patch.object(
            voice, "speak"
        ) as speak, patch.object(voice, "remember_thread"), patch(
            "glitch_voice_hybrid.requests.post", return_value=err
        ):
            handled = voice.handle_local_device_command(text, {})
        self.assertTrue(handled)
        spoken = " ".join(str(c.args[0]) for c in speak.call_args_list if c.args).lower()
        self.assertIn("couldn't turn off", spoken)
        self.assertNotIn("turning off the scrapyard porch lights", spoken)

    def test_handle_command_sends_off_variants_to_ha_not_honesty(self):
        import angus_operator.tools.ha as ha

        spoken = []
        ha_calls = []

        def capture_speak(text, timing=None):
            spoken.append(text)

        def fake_switch(entity_id, action):
            ha_calls.append((entity_id, action))

        for text in OFF_VARIANTS:
            spoken.clear()
            ha_calls.clear()
            with self.subTest(text=text), patch.object(
                voice, "speak", side_effect=capture_speak
            ), patch.object(voice, "speak_from_sentences", lambda *a, **k: None), patch.object(
                voice, "remember_thread"
            ), patch.object(
                voice, "log_command_latency", lambda *a, **k: None
            ), patch.object(
                ha, "call_switch", side_effect=fake_switch
            ), patch.object(
                ha, "entity_state", return_value="off"
            ), patch.object(
                ha, "HA_TOKEN", "test-token"
            ), patch.object(
                voice, "ask_glitch", side_effect=AssertionError("llm")
            ), patch.object(
                voice, "iter_glitch_sentences", side_effect=AssertionError("llm")
            ):
                voice.handle_command(text)
            self.assertTrue(ha_calls, text)
            self.assertEqual(ha_calls[0][1], "off", text)
            self.assertEqual(ha_calls[0][0], "switch.scrapyard_porch_light", text)
            blob = " ".join(spoken).lower()
            self.assertNotIn("didn't run a yard tool", blob, text)

    def test_port_line_does_not_execute_ha(self):
        import angus_operator.tools.ha as ha

        spoken = []
        with patch.object(voice, "speak", side_effect=lambda t, timing=None: spoken.append(t)), patch.object(
            voice, "speak_from_sentences", lambda *a, **k: spoken.extend(a[0] or [])
        ), patch.object(voice, "remember_thread"), patch.object(
            voice, "log_command_latency", lambda *a, **k: None
        ), patch.object(
            ha, "call_switch", side_effect=AssertionError("ha")
        ), patch.object(
            voice, "requests"
        ) as req, patch.object(
            voice, "ask_glitch", return_value={"reply": "ok", "backend": "local", "model": "x"}
        ), patch.object(
            voice, "iter_glitch_sentences", return_value=iter(())
        ):
            req.post.side_effect = AssertionError("ha")
            voice.handle_command("Turn off scrapyard port line.")
        blob = " ".join(spoken).lower()
        self.assertNotIn("turning off the scrapyard porch lights", blob)


if __name__ == "__main__":
    unittest.main()
