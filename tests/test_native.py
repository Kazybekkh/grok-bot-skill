"""Native room regressions: no network, credentials, or live bot actions."""
import base64
import contextlib
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("native_grok", Path(__file__).resolve().parents[1] / "scripts/grokbot.py")
grok = importlib.util.module_from_spec(spec)
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grok)

AGENT = {"id": "room-id", "name": "Demo", "harness": "temporal", "isGroup": True, "memberIds": ["a", "b"]}


def row(entry):
    return {"body": base64.b64encode(json.dumps(entry).encode()).decode()}


class NativeConversationTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(grok, "_decrypt_access_token", side_effect=AssertionError("No credentials")))
        self.stack.enter_context(patch.object(grok, "_gateway", side_effect=AssertionError("No gateway")))
        self.connect = self.stack.enter_context(patch.object(grok, "_connect"))

    def test_send_uses_native_identity_and_requires_explicit_acceptance(self):
        self.connect.return_value = {"delivery": "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_TEMPORAL"}
        self.assertTrue(grok._send_message("synthetic", {}, AGENT, "Draft only"))
        call = self.connect.call_args.args
        self.assertEqual(call[:3], ("synthetic", "aiserver.v1.GrokBotService", "SendGrokBotUserMessage"))
        self.assertEqual(call[3]["agentId"], "room-id")
        self.assertEqual(call[3]["text"], "Draft only")
        self.assertTrue(call[3]["messageId"])
        self.assertEqual(call[3]["source"], "GROK_BOT_CLIENT_SURFACE_DESKTOP")
        for response in ({}, {"delivery": 0}, {"delivery": 4}):
            self.connect.return_value = response
            with self.assertRaises(grok.GrokBotError):
                grok._send_message("synthetic", {}, AGENT, "Draft only")

    def test_uncertain_send_checks_same_identity_without_resending(self):
        self.connect.side_effect = [OSError("synthetic failure"), {"status": "GROK_BOT_SEND_STATUS_ACCEPTED"}]
        self.assertTrue(grok._send_message("synthetic", {}, AGENT, "Draft only"))
        sent, status = [call.args for call in self.connect.call_args_list]
        self.assertEqual(status[2], "GetGrokBotSendStatus")
        self.assertEqual(sent[3]["messageId"], status[3]["messageId"])
        self.assertEqual(self.connect.call_count, 2)

    def test_uncertain_send_never_resends_when_status_unknown(self):
        self.connect.side_effect = [OSError("synthetic failure"), {"status": "GROK_BOT_SEND_STATUS_NOT_FOUND"}]
        with self.assertRaisesRegex(grok.GrokBotError, "not resent"):
            grok._send_message("synthetic", {}, AGENT, "Draft only")
        self.assertEqual([call.args[2] for call in self.connect.call_args_list], ["SendGrokBotUserMessage", "GetGrokBotSendStatus"])

    def test_native_transcript_is_chronological_and_preserves_authors(self):
        first = {"id": "user-1", "kind": "message", "authorId": "user", "content": "Begin"}
        last = {"id": "bot-1", "kind": "send-message", "author": {"id": "a", "name": "Merchant"}, "message": {"type": "text", "content": "Offer"}}
        self.connect.return_value = {"entries": [row(last), row(first)]}
        self.assertEqual(grok._native_transcript("synthetic", "room-id", 80), [first, last])
        self.assertEqual(self.connect.call_args.args[3], {"agentId": "room-id", "limit": 80, "sessionId": ""})

    def test_blob_reads_only_requested_paths_without_exposing_urls(self):
        blob_hash = "a" * 64
        entry = {"id": "bot-1", "kind": "send-message", "message": {"type": "text", "content": "Offer"}}
        signed_url = "https://storage.example.invalid/object?private=signature"
        self.connect.side_effect = [{"entries": [{"blobHash": blob_hash}]}, {"instructions": [
            {"relPath": "other/object", "url": "https://unrelated.invalid/"},
            {"relPath": "blobs/" + blob_hash, "url": signed_url}]}]
        with patch.object(grok, "_read_signed_blob", return_value=json.dumps(entry).encode()) as read:
            result = grok._native_transcript("synthetic", "room-id")
        self.assertEqual(result, [entry])
        read.assert_called_once_with(signed_url)
        self.assertNotIn(signed_url, json.dumps(result))

    def test_bad_blob_paths_and_missing_bodies_are_not_silently_dropped(self):
        for bad in ({"blobHash": "../../secret"}, {"bodyOmitted": True}, {"body": "not-base64"}):
            self.connect.return_value = {"entries": [bad]}
            with self.subTest(bad=bad), self.assertRaises(grok.GrokBotError):
                grok._native_transcript("synthetic", "room-id")

    def test_signed_blob_rejects_plain_http_before_network(self):
        with patch.object(grok.urllib.request, "urlopen", side_effect=AssertionError("No network")) as read:
            with self.assertRaises(grok.GrokBotError):
                grok._read_signed_blob("http://storage.example.invalid/object")
        read.assert_not_called()

    def test_group_info_finds_server_only_room(self):
        def native_record(agent_id, *, group=False):
            return {"agentId": agent_id, "id": "different-server-id", "name": agent_id,
                    "harness": "temporal", "kind": 2 if group else 1,
                    "memberAgentIds": ["a", "b"] if group else []}
        self.connect.return_value = {"agents": [native_record("room-id", group=True), native_record("a"), native_record("b")]}
        with patch.object(grok, "_session", return_value=("synthetic", {})), patch.object(grok, "_list_agents", return_value=[]):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                grok.cmd_group_info(SimpleNamespace(id="room-id", name=None))
        result = json.loads(stream.getvalue())
        self.assertEqual(result["group"]["id"], "room-id")
        self.assertEqual([member["id"] for member in result["members"]], ["a", "b"])

    def test_native_chat_with_unknown_runtime_does_not_claim_completed(self):
        with patch.object(grok, "_session", return_value=("synthetic", {})), \
             patch.object(grok, "_resolve_agent", return_value=AGENT), \
             patch.object(grok, "_read_transcript", return_value=[]), \
             patch.object(grok, "_send_message", return_value=True):
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                grok.cmd_chat(SimpleNamespace(id="room-id", name=None, prompt="Draft", timeout=0, poll=1))
        self.assertTrue(json.loads(stream.getvalue())["stillRunning"])


if __name__ == "__main__":
    unittest.main()
