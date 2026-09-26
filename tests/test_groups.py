"""Native group regression tests. No Keychain, network, files or bot actions."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "grokbot.py"
spec = importlib.util.spec_from_file_location("grokbot_groups", SCRIPT)
grokbot = importlib.util.module_from_spec(spec)
# Import-time version discovery also stays independent of the installed app.
with patch.object(Path, "exists", return_value=False):
    spec.loader.exec_module(grokbot)


def bot(bot_id, name=None, **extra):
    return {"id": bot_id, "name": name or bot_id, "isGroup": False,
            "viewerIsOwner": True, "harness": "box", **extra}


def room(room_id, name, ids, **extra):
    return {"id": room_id, "name": name, "isGroup": True,
            "memberIds": list(ids), "viewerIsOwner": True, "harness": "box", **extra}


class NativeGroupTests(unittest.TestCase):
    def setUp(self):
        self.agents = [bot("a", "A"), bot("b", "B"), bot("c", "C"), bot("d", "D")]
        self.native_agents = []
        self.gateway = Mock(side_effect=self.fake_gateway)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(grokbot, "_session", return_value=("synthetic-access", {"test": "box"})))
        self.stack.enter_context(patch.object(grokbot, "_setup_lock", side_effect=contextlib.nullcontext))
        self.stack.enter_context(patch.object(grokbot, "_list_agents", side_effect=lambda box: copy.deepcopy(self.agents)))
        self.stack.enter_context(patch.object(grokbot, "_gateway", self.gateway))
        self.connect = self.stack.enter_context(patch.object(grokbot, "_connect", side_effect=self.fake_connect))
        self.stack.enter_context(patch.object(grokbot, "_decrypt_access_token", side_effect=AssertionError("Credential access forbidden in tests")))

    def fake_connect(self, access, service, method, body):
        if method == "ListGrokBotAgents":
            return {"agents": copy.deepcopy(self.native_agents)}
        if method in ("CreateGrokBotTemporalAgent", "CreateGrokBotRoom"):
            raw = {"agentId": body["agentId"], "name": body["name"], "description": body["description"],
                   "title": body.get("title", ""), "harness": "temporal", "viewerIsOwner": True,
                   "kind": "GROK_BOT_AGENT_KIND_ROOM" if method == "CreateGrokBotRoom" else "GROK_BOT_AGENT_KIND_AGENT"}
            if method == "CreateGrokBotRoom":
                raw["memberAgentIds"] = list(body["memberAgentIds"])
            self.native_agents.append(raw)
            return {"agent": copy.deepcopy(raw)}
        raise AssertionError(f"Unexpected remote RPC: {method}")

    def rpc_mutations(self):
        return [call for call in self.connect.call_args_list if call.args[2] != "ListGrokBotAgents"]

    def assert_no_rpc_mutations(self):
        self.assertEqual(self.rpc_mutations(), [])

    def fake_gateway(self, box, route, body):
        if route == "/api/createGroup":
            created = room("new-room", body["name"], body["memberAgentIds"])
            self.agents.append(created)
            return {"agent": copy.deepcopy(created)}
        if route == "/api/createAgent":
            created = bot(f"created-{len(self.agents)}", body["name"], description=body["description"])
            self.agents.append(created)
            return {"agent": copy.deepcopy(created)}
        if route == "/api/updateAgent":
            existing = next(agent for agent in self.agents if agent["id"] == body["id"])
            existing.update(body["profile"])
            return {"agent": copy.deepcopy(existing)}
        raise AssertionError(f"Unexpected gateway action: {route}")

    def output(self, command, args):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            command(args)
        return json.loads(stream.getvalue())

    def create(self, ids=None, *, name="Demo room", reuse=False, names=None):
        return self.output(grokbot.cmd_group_create, SimpleNamespace(
            name=name, description="Fictional test room", member_id=ids if ids is not None else ["a", "b"],
            member_name=names, reuse=reuse))

    def setup_demo(self, name="Demo room", config=None):
        return self.output(grokbot.cmd_setup_demo, SimpleNamespace(name=name, config_json=json.dumps(config or {})))

    def demo_bots(self):
        return [bot(role, name, title=title, description=f"Existing instructions for {role}")
                for role, name, title, _ in grokbot.DEMO_ROLES]

    def test_creates_native_group_with_exact_existing_members(self):
        result = self.create(["a", "c", "b"])
        self.assertTrue(result["group"]["isGroup"])
        self.assertEqual(result["group"]["memberIds"], ["a", "c", "b"])
        self.assertEqual([member["id"] for member in result["members"]], ["a", "c", "b"])
        self.assertTrue(result["created"])
        self.gateway.assert_called_once()
        self.assertEqual(self.gateway.call_args.args[1], "/api/createGroup")
        self.assertEqual(self.gateway.call_args.args[2]["memberAgentIds"], ["a", "c", "b"])

    def test_reuses_only_exact_name_and_members_independent_of_order(self):
        self.agents.append(room("existing", "Demo room", ["b", "a"]))
        result = self.create(["a", "b"], name="demo ROOM", reuse=True)
        self.assertEqual(result["group"]["id"], "existing")
        self.assertFalse(result["created"])
        self.gateway.assert_not_called()
        with self.assertRaises(grokbot.GrokBotError):
            self.create(["a", "c"], reuse=True)
        self.gateway.assert_not_called()

    def test_reuse_rejects_malformed_duplicate_group_membership(self):
        self.agents.append(room("malformed", "Demo room", ["a", "b", "a"]))
        with self.assertRaises(grokbot.GrokBotError):
            self.create(["a", "b"], reuse=True)
        self.gateway.assert_not_called()

    def test_duplicate_unknown_or_group_members_are_rejected_before_creation(self):
        self.agents.append(room("group-member", "Another room", ["a", "b"]))
        for ids in (["a", "a"], ["a"], ["a", "missing"], ["a", "group-member"], [str(i) for i in range(7)]):
            with self.subTest(ids=ids), self.assertRaises(grokbot.GrokBotError):
                self.create(ids)
        self.gateway.assert_not_called()

    def test_nonowned_members_and_groups_are_not_reused(self):
        self.agents[0]["viewerIsOwner"] = False
        with self.assertRaises(grokbot.GrokBotError):
            self.create()
        self.agents[0]["viewerIsOwner"] = True
        self.agents.append(room("shared", "Demo room", ["a", "b"], viewerIsOwner=False))
        with self.assertRaises(grokbot.GrokBotError):
            self.create(reuse=True)
        self.gateway.assert_not_called()

    def test_unknown_harness_is_rejected_before_group_mutation(self):
        for harness in ("unverified-future-harness", None, ""):
            self.agents[0]["harness"] = harness
            with self.subTest(harness=harness), self.assertRaises(grokbot.GrokBotError):
                self.create()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_ambiguous_member_or_room_name_never_chooses_first(self):
        self.agents.append(bot("other-a", "A"))
        with self.assertRaises(grokbot.GrokBotError):
            self.create([], names=["A", "B"])
        self.agents.extend([room("r1", "Demo room", ["a", "b"]), room("r2", "demo ROOM", ["a", "b"])])
        with self.assertRaises(grokbot.GrokBotError):
            self.create(reuse=True)
        self.gateway.assert_not_called()

    def test_uncertain_create_reconciles_roster_without_resending(self):
        def lost_response(box, route, body):
            self.fake_gateway(box, route, body)
            raise OSError("Synthetic connection dropped after creation")
        self.gateway.side_effect = lost_response
        result = self.create()
        self.assertEqual(result["group"]["id"], "new-room")
        self.assertTrue(result["created"])
        self.gateway.assert_called_once()

    def test_uncertain_create_without_roster_confirmation_does_not_retry(self):
        self.gateway.side_effect = OSError("Synthetic connection dropped")
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be confirmed"):
            self.create()
        self.gateway.assert_called_once()
        self.assertFalse(any(agent["isGroup"] for agent in self.agents))

    def test_success_response_must_match_fresh_roster_identity(self):
        def wrong_id(box, route, body):
            self.fake_gateway(box, route, body)
            return {"agent": room("different", body["name"], body["memberAgentIds"])}
        self.gateway.side_effect = wrong_id
        with self.assertRaisesRegex(grokbot.GrokBotError, "could not be verified"):
            self.create()
        self.gateway.assert_called_once()

    def test_temporal_members_use_native_room_rpc_and_roundtrip_identity(self):
        self.agents[0]["harness"] = self.agents[1]["harness"] = "temporal"
        result = self.create()
        mutations = self.rpc_mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(mutations[0].args[2], "CreateGrokBotRoom")
        payload = mutations[0].args[3]
        self.assertEqual(payload["memberAgentIds"], ["a", "b"])
        self.assertEqual(payload["humanMemberUserIds"], [])
        self.assertEqual(result["group"]["id"], payload["agentId"])
        self.gateway.assert_not_called()

    def test_temporal_lost_response_reconciles_without_resending(self):
        self.agents[0]["harness"] = self.agents[1]["harness"] = "temporal"
        def lost_response(access, service, method, body):
            result = self.fake_connect(access, service, method, body)
            if method == "CreateGrokBotRoom":
                raise OSError("Synthetic lost native room response")
            return result
        self.connect.side_effect = lost_response
        result = self.create()
        mutations = self.rpc_mutations()
        self.assertEqual(len(mutations), 1)
        self.assertEqual(result["group"]["id"], mutations[0].args[3]["agentId"])
        self.gateway.assert_not_called()

    def test_mixed_runtime_group_is_rejected_before_mutation(self):
        self.agents[0]["harness"] = "temporal"
        with self.assertRaises(grokbot.GrokBotError):
            self.create()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_fresh_setup_creates_four_independent_temporal_bots_without_kickoff(self):
        self.agents = []
        result = self.setup_demo()
        mutations = self.rpc_mutations()
        self.assertEqual([call.args[2] for call in mutations], ["CreateGrokBotTemporalAgent"] * 4 + ["CreateGrokBotRoom"])
        payloads = [call.args[3] for call in mutations[:4]]
        self.assertEqual(len({payload["agentId"] for payload in payloads}), 4)
        for payload in payloads:
            self.assertIs(payload["kickstartRequested"], False)
            self.assertIs(payload["introductionSuppressed"], True)
            self.assertEqual(payload["harness"], "GROK_BOT_AGENT_HARNESS_KIND_TEMPORAL")
        self.assertEqual(len(result["created"]["bots"]), 4)
        self.assertTrue(result["created"]["group"])
        self.assertEqual(set(result["group"]["memberIds"]), set(result["created"]["bots"]))
        self.gateway.assert_not_called()
        self.connect.reset_mock()
        again = self.setup_demo()
        self.assertEqual(again["group"]["id"], result["group"]["id"])
        self.assertEqual(again["created"], {"bots": [], "group": False})
        self.assert_no_rpc_mutations()

    def test_partial_setup_retry_reuses_uncertain_created_bot(self):
        self.agents = []
        dropped = False
        def lost_response(access, service, method, body):
            nonlocal dropped
            result = self.fake_connect(access, service, method, body)
            if method == "CreateGrokBotTemporalAgent" and not dropped:
                dropped = True
                raise OSError("Synthetic lost response after bot was created")
            return result
        self.connect.side_effect = lost_response
        with self.assertRaisesRegex(grokbot.GrokBotError, "uncertain"):
            self.setup_demo()
        self.assertEqual(len(self.rpc_mutations()), 1)
        first_id = self.native_agents[0]["agentId"]
        self.connect.reset_mock()
        result = self.setup_demo()
        self.assertEqual([call.args[2] for call in self.rpc_mutations()], ["CreateGrokBotTemporalAgent"] * 3 + ["CreateGrokBotRoom"])
        self.assertIn(first_id, result["group"]["memberIds"])
        self.assertEqual(len(result["created"]["bots"]), 3)
        self.assertEqual(len({raw["name"] for raw in self.native_agents if raw["kind"] != "GROK_BOT_AGENT_KIND_ROOM"}), 4)
        self.gateway.assert_not_called()

    def test_box_setup_preserves_runtime_and_never_kicks_off(self):
        self.agents = self.demo_bots()[:1]
        result = self.setup_demo()
        self.assertEqual(len(result["created"]["bots"]), 3)
        creates = [call for call in self.gateway.call_args_list if call.args[1] == "/api/createAgent"]
        self.assertEqual(len(creates), 3)
        self.assertTrue(all(call.args[2]["isKickstartRequested"] is False for call in creates))
        self.assertFalse(any(call.args[1] == "/api/sendPrompt" for call in self.gateway.call_args_list))
        self.assert_no_rpc_mutations()

    def test_setup_reuses_profiles_and_room_without_sending_or_rewriting(self):
        self.agents = self.demo_bots()
        original = copy.deepcopy(self.agents)
        self.agents.append(room("existing", "Demo room", [agent["id"] for agent in self.agents]))
        result = self.setup_demo(config={"product": "Linen coats", "denimBudgetPence": 650000})
        self.assertEqual(result["group"]["id"], "existing")
        self.assertEqual(result["created"], {"bots": [], "group": False})
        self.assertEqual({member["role"] for member in result["members"]}, {"merchant", "denim", "bargain", "premium"})
        self.assertIn("Linen coats", result["brief"])
        self.assertIn("6500.00", result["brief"])
        self.assertEqual(self.agents[:4], original)
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_setup_preflights_all_role_names_before_creating_anything(self):
        premium = grokbot.DEMO_ROLES[-1][1]
        self.agents = [bot("p1", premium), bot("p2", premium)]
        with self.assertRaises(grokbot.GrokBotError):
            self.setup_demo()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_setup_preflights_existing_room_membership_before_creating(self):
        self.agents = [room("conflict", "Demo room", ["other", "people"])]
        with self.assertRaises(grokbot.GrokBotError):
            self.setup_demo()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_setup_preflights_nonowned_existing_personas(self):
        self.agents = [bot("denim", grokbot.DEMO_ROLES[1][1], viewerIsOwner=False)]
        with self.assertRaises(grokbot.GrokBotError):
            self.setup_demo()
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()

    def test_config_rejects_boolean_quantities_unknown_fields_and_invalid_bounds(self):
        for config in ({"quantity": True}, {"quantity": 10001}, {"denimBudgetPence": 1000000001},
                       {"floorPricePence": 5000}, {"premiumMaxQuantity": 301}, {"gatewayUrl": "not-accepted"}):
            with self.subTest(config=config), self.assertRaises(grokbot.GrokBotError):
                self.setup_demo(config=config)
        self.gateway.assert_not_called()
        self.assert_no_rpc_mutations()


if __name__ == "__main__":
    unittest.main()
