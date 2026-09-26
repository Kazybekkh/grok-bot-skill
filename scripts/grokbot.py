#!/usr/bin/env python3
"""Talk to Grok Bot teammates and create new ones. Never prints secrets."""

from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import plistlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

SECRETS_PATH = Path.home() / "Library/Application Support/Grok Bot/sand-secrets.json"
BACKEND = "https://api2.cursor.sh"
def _client_version() -> str:
    for app_dir in (Path("/Applications"), Path.home() / "Applications"):
        info = app_dir / "Grok Bot.app/Contents/Info.plist"
        if info.exists():
            with info.open("rb") as handle:
                version = plistlib.load(handle).get("CFBundleShortVersionString")
            if isinstance(version, str) and version:
                return version
    return "0.18.0"


CLIENT_VERSION = _client_version()


class GrokBotError(RuntimeError):
    pass


def _die(message: str, code: int = 1) -> None:
    print(json.dumps({"error": message}), file=sys.stderr)
    raise SystemExit(code)


def _decrypt_access_token() -> str:
    if not SECRETS_PATH.exists():
        raise GrokBotError(
            "Grok Bot is not signed in on this Mac. Open Grok Bot and sign in with Cursor first."
        )
    secrets = json.loads(SECRETS_PATH.read_text())
    stored = secrets.get("cursor-access-token")
    # Recent Grok Bot releases keep scoped secrets under the active account.
    # Never select an arbitrary saved account or fall back to a stale legacy
    # token when the multi-account store exists.
    if "cursor-accounts" in secrets:
        try:
            accounts = json.loads(secrets["cursor-accounts"])
            active = accounts.get("active")
            stored = accounts.get("accounts", {}).get(active, {}).get("cursor-access-token")
        except (ValueError, TypeError, AttributeError):
            raise GrokBotError("Grok Bot account storage is invalid. Open Grok Bot and sign in with Cursor first.") from None
    if not isinstance(stored, str) or not stored:
        raise GrokBotError("Grok Bot access token is missing or in an unexpected format.")
    # Account entries store Electron safeStorage ciphertext directly; older
    # single-account entries wrap that same ciphertext with an account scope.
    try:
        if stored.startswith("scoped:v1:"):
            rest = stored[len("scoped:v1:") :]
            stored = rest[rest.index(":") + 1 :]
        raw = base64.b64decode(stored, validate=True)
    except ValueError:
        raise GrokBotError("Grok Bot access token is in an unexpected format.") from None
    if not raw.startswith(b"v10"):
        raise GrokBotError("Grok Bot access token is not in the expected v10 envelope.")
    try:
        password = subprocess.check_output(
            [
                "security",
                "find-generic-password",
                "-s",
                "Grok Bot Safe Storage",
                "-a",
                "Grok Bot Key",
                "-w",
            ],
            text=True,
        ).rstrip("\n")
    except subprocess.CalledProcessError as exc:
        raise GrokBotError(
            "Could not read Grok Bot Safe Storage from Keychain. Unlock the login keychain and retry."
        ) from exc
    key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)
    proc = subprocess.run(
        [
            "openssl",
            "enc",
            "-aes-128-cbc",
            "-d",
            "-K",
            key.hex(),
            "-iv",
            (b" " * 16).hex(),
            "-nopad",
        ],
        input=raw[3:],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise GrokBotError("Failed to decrypt the Grok Bot access token.")
    plaintext = proc.stdout
    pad = plaintext[-1]
    if pad < 1 or pad > 16 or plaintext[-pad:] != bytes([pad]) * pad:
        raise GrokBotError("Failed to unpad the Grok Bot access token.")
    return plaintext[:-pad].decode()


def _connect(access_token: str, service: str, method: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{BACKEND}/{service}/{method}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Connect-Protocol-Version": "1",
        "x-cursor-client-type": "sand",
        "x-cursor-client-version": CLIENT_VERSION,
        "x-sand-box-namespace": "prod",
        "x-ghost-mode": "false",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(body or {}).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise GrokBotError(f"{service}/{method} failed HTTP {exc.code}: {_safe_error(detail)}") from exc


def _safe_error(raw: str) -> str:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:240]
    message = parsed.get("message") or parsed.get("code") or "request failed"
    debug = None
    details = parsed.get("details")
    if isinstance(details, list) and details:
        debug = (details[0].get("debug") or {}).get("error")
    return f"{message}" + (f" ({debug})" if debug else "")


def _native_running(access_token: str, agent_id: str) -> bool | None:
    """Read one authoritative live-state snapshot, then close the native stream.

    Verified against desktop 0.59.1 WatchGrokBotTranscripts and its agentState
    snapshot. Connect framing: https://connectrpc.com/docs/protocol/.
    A complete snapshot with no running sessions means idle; a missing snapshot
    or transport failure means unknown, never idle.
    """
    body = json.dumps({"cursors": [{"agentId": agent_id, "generation": 0,
                                   "afterUpdatedSeq": "0", "sessionId": ""}],
                       "includeUnlistedAgents": False, "inlineBodyMaxBytes": 0}).encode()
    request = urllib.request.Request(
        f"{BACKEND}/aiserver.v1.GrokBotService/WatchGrokBotTranscripts",
        data=b"\x00" + len(body).to_bytes(4, "big") + body,
        headers={"Authorization": f"Bearer {access_token}",
                 "Content-Type": "application/connect+json", "Connect-Protocol-Version": "1",
                 "Connect-Timeout-Ms": "5000", "x-cursor-client-type": "sand",
                 "x-cursor-client-version": CLIENT_VERSION, "x-sand-box-namespace": "prod",
                 "x-ghost-mode": "false"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            def read_exact(size):
                result = b""
                while len(result) < size:
                    part = response.read(size - len(result))
                    if not part:
                        raise ValueError("incomplete frame")
                    result += part
                return result
            for _ in range(32):
                header = read_exact(5)
                size = int.from_bytes(header[1:], "big")
                if header[0] != 0 or size > 2 * 1024 * 1024:
                    return None
                frame = json.loads(read_exact(size))
                state = frame.get("agentState")
                if isinstance(state, dict) and state.get("snapshot") is True:
                    live = state.get("live", [])
                    if not isinstance(live, list) or any(not isinstance(row, dict) for row in live):
                        return None
                    return any(row.get("agentId") == agent_id and
                               (row.get("isRunning") is True or row.get("hasRunningSubagents") is True)
                               for row in live)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    return None


def _ensure_box(access_token: str) -> dict[str, str]:
    box = _connect(access_token, "aiserver.v1.GrokBotService", "EnsureSandBox", {})
    gateway_url = box.get("gatewayUrl") or box.get("gateway_url")
    gateway_token = box.get("gatewayToken") or box.get("gateway_token")
    network_token = box.get("networkToken") or box.get("network_token")
    if not gateway_url or not gateway_token or not network_token:
        raise GrokBotError("EnsureSandBox did not return a gateway URL and tokens. Is the computer still starting?")
    return {
        "gateway_url": str(gateway_url).rstrip("/"),
        "gateway_token": str(gateway_token),
        "network_token": str(network_token),
        "cluster": str(box.get("cluster") or ""),
        "pod_id": str(box.get("podId") or box.get("pod_id") or ""),
    }


def _gateway(box: dict[str, str], path: str, body: dict[str, Any] | None = None) -> Any:
    url = box["gateway_url"] + path
    headers = {
        "Authorization": f"Bearer {box['gateway_token']}",
        "x-anyrun-network-token": box["network_token"],
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url,
        data=json.dumps({} if body is None else body).encode(),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise GrokBotError(f"{path} failed HTTP {exc.code}: {_safe_error(detail)}") from exc


def _session() -> tuple[str, dict[str, str]]:
    access = _decrypt_access_token()
    return access, _ensure_box(access)


def _summarize_agent(agent: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    description = agent.get("description") or ""
    preview = agent.get("lastMessagePreview") or ""
    if not full and len(description) > 180:
        description = description[:177] + "..."
    if not full and len(preview) > 180:
        preview = preview[:177] + "..."
    return {
        "id": agent.get("id"),
        "name": agent.get("name"),
        "title": agent.get("title") or "",
        "description": description,
        "isActive": agent.get("isActive"),
        "isRunning": agent["isRunning"]
        if "isRunning" in agent
        else agent.get("isRunningTurn"),
        "hasUnread": agent.get("hasUnread"),
        "lastMessagePreview": preview,
        "origin": agent.get("origin"),
        "isGroup": agent.get("isGroup") is True,
        "memberIds": agent.get("memberIds") or [],
    }


def _list_agents(box: dict[str, str]) -> list[dict[str, Any]]:
    rows = _gateway(box, "/api/listAgents", {})
    if isinstance(rows, dict):
        rows = rows.get("agents") or rows.get("rows") or []
    if not isinstance(rows, list):
        raise GrokBotError("listAgents returned an unexpected payload.")
    return [row for row in rows if isinstance(row, dict)]


def _resolve_agent(box: dict[str, str], *, agent_id: str | None, name: str | None, access=None) -> dict[str, Any]:
    agents = _setup_roster(box, access) if access else _list_agents(box)
    if agent_id:
        for agent in agents:
            if agent.get("id") == agent_id:
                return agent
        raise GrokBotError(f"No Grok Bot with id {agent_id}.")
    if name:
        matches = [agent for agent in agents if str(agent.get("name") or "").lower() == name.lower()]
        if not matches:
            available = ", ".join(str(agent.get("name") or "?") for agent in agents) or "(none)"
            raise GrokBotError(f"No Grok Bot named {name!r}. Available: {available}")
        if len(matches) > 1:
            raise GrokBotError(f"Multiple Grok Bots named {name!r}; pass --id.")
        return matches[0]
    raise GrokBotError("Pass --id or --name.")


def _extract_agent(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict) and isinstance(payload.get("agent"), dict):
        return payload["agent"]
    if isinstance(payload, dict) and payload.get("id"):
        return payload
    raise GrokBotError("The gateway did not return an agent object.")


def _transcript_entries(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("entries", "items", "messages", "transcript"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _entry_text(entry: dict[str, Any]) -> str:
    for key in ("text", "content", "preview", "message"):
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, dict):
            nested = value.get("text") or value.get("content")
            if isinstance(nested, str) and nested.strip():
                return nested
    return ""


def cmd_status(_: argparse.Namespace) -> None:
    access = _decrypt_access_token()
    access_status = _connect(access, "aiserver.v1.DashboardService", "GetSandAccessStatus", {})
    run_state = _connect(access, "aiserver.v1.GrokBotService", "GetSandBoxRunState", {})
    box = _ensure_box(access)
    try:
        health = _gateway_get(box, "/health")
    except GrokBotError as exc:
        health = {"error": str(exc)}
    print(
        json.dumps(
            {
                "access": access_status.get("state") or access_status,
                "box": run_state.get("state") or run_state,
                "cluster": box["cluster"],
                "podIdPresent": bool(box["pod_id"]),
                "health": {
                    "ok": bool(isinstance(health, dict) and health.get("ok")),
                    "isBusy": health.get("isBusy") if isinstance(health, dict) else None,
                    "activeAgentId": health.get("activeAgentId") if isinstance(health, dict) else None,
                },
            },
            indent=2,
        )
    )


def _gateway_get(box: dict[str, str], path: str) -> Any:
    url = box["gateway_url"] + path
    headers = {
        "Authorization": f"Bearer {box['gateway_token']}",
        "x-anyrun-network-token": box["network_token"],
    }
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise GrokBotError(f"{path} failed HTTP {exc.code}: {_safe_error(detail)}") from exc


def cmd_list(args: argparse.Namespace) -> None:
    access, box = _session()
    agents = _setup_roster(box, access)
    print(json.dumps([_summarize_agent(agent, full=args.full) for agent in agents], indent=2))


def cmd_create(args: argparse.Namespace) -> None:
    _, box = _session()
    existing = [
        agent
        for agent in _list_agents(box)
        if str(agent.get("name") or "").lower() == args.name.lower()
    ]
    if existing and not args.force:
        raise GrokBotError(
            f"A Grok Bot named {args.name!r} already exists ({existing[0].get('id')}). "
            "Use a different name, or pass --force to create another."
        )
    created = _gateway(
        box,
        "/api/createAgent",
        {
            "name": args.name,
            "description": args.description or "",
            "origin": "user",
            "isKickstartRequested": not args.no_kickstart,
        },
    )
    agent = _extract_agent(created)
    agent_id = agent.get("id")
    if agent_id and (args.title or args.description):
        updated = _gateway(
            box,
            "/api/updateAgent",
            {
                "id": agent_id,
                "profile": {
                    "name": args.name,
                    "title": args.title or "",
                    "description": args.description or "",
                },
            },
        )
        agent = _extract_agent(updated) if isinstance(updated, dict) else agent
    print(json.dumps(_summarize_agent(agent, full=True), indent=2))


def cmd_update(args: argparse.Namespace) -> None:
    _, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name)
    profile = {
        "name": args.rename or agent.get("name") or "",
        "title": args.title if args.title is not None else agent.get("title") or "",
        "description": args.description
        if args.description is not None
        else agent.get("description") or "",
    }
    updated = _gateway(box, "/api/updateAgent", {"id": agent["id"], "profile": profile})
    print(json.dumps(_summarize_agent(_extract_agent(updated), full=True), indent=2))


def _send_message(access, box, agent, prompt):
    if _harness(agent) != "temporal":
        result = _gateway(box, "/api/sendPrompt", {"agentId": agent["id"], "prompt": prompt})
        return bool(isinstance(result, dict) and result.get("accepted", True))
    message_id = str(uuid.uuid4())
    body = {"agentId": agent["id"], "messageId": message_id, "text": prompt,
            "sentAtMs": str(int(time.time() * 1000)), "isFork": False,
            "attachmentPaths": [], "attachmentNames": [],
            "source": "GROK_BOT_CLIENT_SURFACE_DESKTOP", "sessionId": ""}
    try:
        result = _connect(access, "aiserver.v1.GrokBotService", "SendGrokBotUserMessage", body)
    except (GrokBotError, OSError, ValueError):
        # Reconcile this exact send identity. Never repeat the prompt on an
        # uncertain response or generate a second message ID automatically.
        try:
            status = _connect(access, "aiserver.v1.GrokBotService", "GetGrokBotSendStatus",
                              {"agentId": agent["id"], "messageId": message_id, "sessionId": ""})
            if status.get("status") in (2, "GROK_BOT_SEND_STATUS_ACCEPTED"):
                return True
            if status.get("status") in (3, "GROK_BOT_SEND_STATUS_REJECTED"):
                raise GrokBotError("Grok Bot refused the message.")
        except (GrokBotError, OSError, ValueError, AttributeError):
            pass
        raise GrokBotError("Message delivery could not be confirmed. Inspect the group before retrying; the message was not resent.") from None
    delivery = result.get("delivery") if isinstance(result, dict) else None
    if delivery in (1, 2, 3, "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_BOX",
                    "GROK_BOT_USER_MESSAGE_DELIVERY_ACCEPTED_TEMPORAL", "GROK_BOT_USER_MESSAGE_DELIVERY_DUPLICATE"):
        return True
    if delivery in (4, "GROK_BOT_USER_MESSAGE_DELIVERY_REFUSED"):
        raise GrokBotError("Grok Bot refused the message. Check the group and account access.")
    raise GrokBotError("Message delivery could not be confirmed. Inspect the group before retrying.")


def _read_signed_blob(url):
    """Read only the HTTPS object URL returned by Grok; never forward auth."""
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise GrokBotError("Grok returned an invalid transcript object URL.")
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=30) as response:
            if urllib.parse.urlsplit(response.geturl()).scheme != "https":
                raise GrokBotError("Grok returned an invalid transcript object redirect.")
            body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise GrokBotError("A transcript message is too large to display.")
            return body
    except (OSError, ValueError):
        raise GrokBotError("A transcript message could not be loaded. Retry reading the conversation.") from None


def _native_transcript(access, agent_id, limit=80):
    payload = _connect(access, "aiserver.v1.GrokBotService", "ListGrokBotTranscriptEntries",
                       {"agentId": agent_id, "limit": max(1, min(limit or 500, 500)), "sessionId": ""})
    rows = payload.get("entries", []) if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        raise GrokBotError("Grok returned an invalid native transcript.")
    hashes = set()
    for row in rows:
        if row.get("body") is None and row.get("blobHash"):
            value = row["blobHash"]
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value):
                raise GrokBotError("Grok returned an invalid transcript object reference.")
            hashes.add(value)
    blobs = {}
    if hashes:
        requested = {"blobs/" + value for value in hashes}
        signed = _connect(access, "aiserver.v1.GrokBotService", "PresignSandBoxStoreReads",
                          {"relPaths": sorted(requested)})
        instructions = signed.get("instructions", []) if isinstance(signed, dict) else []
        for instruction in instructions:
            if not isinstance(instruction, dict) or instruction.get("relPath") not in requested:
                continue
            if not isinstance(instruction.get("url"), str):
                raise GrokBotError("Grok returned an invalid transcript object URL.")
            blobs[instruction["relPath"][6:]] = _read_signed_blob(instruction["url"])
    entries = []
    for row in reversed(rows):
        try:
            if row.get("body") is not None:
                raw = base64.b64decode(row["body"], validate=True)
            else:
                raw = blobs.get(row.get("blobHash"))
            if raw is None:
                raise ValueError("missing body")
            entry = json.loads(raw.decode("utf-8"))
            if not isinstance(entry, dict) or not isinstance(entry.get("id"), str) or not isinstance(entry.get("kind"), str):
                raise ValueError("invalid entry")
            entries.append(entry)
        except (ValueError, TypeError, UnicodeError):
            raise GrokBotError("A native transcript entry could not be decoded. Retry reading the conversation.") from None
    return entries


def _read_transcript(access, box, agent, limit=80):
    if _harness(agent) == "temporal":
        return _native_transcript(access, agent["id"], limit)
    entries = _transcript_entries(_gateway(box, "/api/getAgentTranscript", {"id": agent["id"]}))
    return entries[-limit:] if limit else entries


def cmd_send(args: argparse.Namespace) -> None:
    access, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name, access=access)
    accepted = _send_message(access, box, agent, args.prompt)
    print(
        json.dumps(
            {
                "accepted": accepted,
                "agent": _summarize_agent(agent),
            },
            indent=2,
        )
    )


def cmd_transcript(args: argparse.Namespace) -> None:
    access, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name, access=access)
    entries = _read_transcript(access, box, agent, args.limit)
    if _harness(agent) == "temporal":
        agent = {**agent, "isRunning": _native_running(access, agent["id"])}
    print(
        json.dumps(
            {
                "agent": _summarize_agent(agent),
                "entries": [
                    {
                        "id": entry.get("id") or entry.get("entryId"),
                        "kind": entry.get("kind") or entry.get("type"),
                        "author": entry.get("authorId") or entry.get("role") or entry.get("author"),
                        "text": _entry_text(entry),
                        "streaming": entry.get("streaming") is True,
                    }
                    for entry in entries
                ],
            },
            indent=2,
        )
    )


def cmd_chat(args: argparse.Namespace) -> None:
    access, box = _session()
    agent = _resolve_agent(box, agent_id=args.id, name=args.name, access=access)
    before = _read_transcript(access, box, agent)
    before_ids = {entry.get("id") or entry.get("entryId") for entry in before}
    if not _send_message(access, box, agent, args.prompt):
        raise GrokBotError("sendPrompt was not accepted.")
    deadline = time.time() + args.timeout
    latest = agent
    native = _harness(agent) == "temporal"
    completion_known = False
    while time.time() < deadline:
        time.sleep(args.poll)
        agents = _setup_roster(box, access)
        latest = next((row for row in agents if row.get("id") == agent["id"]), latest)
        running = bool(latest.get("isRunning") or latest.get("isRunningTurn") or latest.get("isComposingMessage"))
        if native:
            after = _read_transcript(access, box, agent)
            replied = any(entry.get("kind") == "send-message" and entry.get("streaming") is not True
                          and _entry_text(entry) and entry.get("id") not in before_ids for entry in after)
            completion_known = replied and latest.get("isRunning") is False and not running
        else:
            completion_known = not running
        if completion_known:
            break
    after = _read_transcript(access, box, agent)
    new_entries = [
        entry
        for entry in after
        if (entry.get("id") or entry.get("entryId")) not in before_ids
    ]
    print(
        json.dumps(
            {
                "agent": _summarize_agent(latest, full=True),
                "stillRunning": (native and not completion_known) or bool(
                    latest.get("isRunning") or latest.get("isRunningTurn") or latest.get("isComposingMessage")
                ),
                "newEntries": [
                    {
                        "id": entry.get("id") or entry.get("entryId"),
                        "kind": entry.get("kind") or entry.get("type"),
                        "author": entry.get("authorId") or entry.get("role") or entry.get("author"),
                        "text": _entry_text(entry),
                    }
                    for entry in new_entries
                ],
            },
            indent=2,
        )
    )


@contextmanager
def _setup_lock():
    """Serialize group setup across CLI processes, without storing credentials."""
    directory = Path.home() / ".cache" / "grok-bot-skill"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (directory / "setup.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GrokBotError("Another group setup is running. Wait for it to finish.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _name(value: str, label: str = "Name") -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120:
        raise GrokBotError(f"{label} must contain 1–120 characters.")
    if any(ord(char) < 32 for char in value):
        raise GrokBotError(f"{label} must not contain control characters.")
    return value.strip()


def _harness(agent):
    value = agent.get("harness", "box")
    if value not in ("box", "temporal"):
        raise GrokBotError("This bot uses an unsupported Grok runtime.")
    return value


def _box_owned(agent: dict[str, Any]) -> None:
    if agent.get("viewerIsOwner") is False:
        raise GrokBotError("Choose bots and groups owned by your current Grok account.")
    _harness(agent)


def _temporal_agent(raw):
    """Normalize native control-plane records; id is NOT the bot UUID."""
    if not isinstance(raw, dict) or not raw.get("agentId") or raw.get("harness") != "temporal":
        raise GrokBotError("Grok returned an invalid native agent record.")
    group = raw.get("kind") in (2, "ROOM", "GROK_BOT_AGENT_KIND_ROOM")
    return {"id": raw["agentId"], "name": raw.get("name"), "title": raw.get("title", ""),
            "description": raw.get("description", ""), "harness": "temporal", "isGroup": group,
            "memberIds": raw.get("memberAgentIds", []) if group else [],
            "viewerIsOwner": raw.get("viewerIsOwner", True)}


def _setup_roster(box, access, *, strict_native=False):
    """Use native identities/membership while retaining gateway runtime state."""
    rows = _list_agents(box)
    if not access:
        return rows
    try:
        native = _connect(access, "aiserver.v1.GrokBotService", "ListGrokBotAgents", {})
        if not isinstance(native, dict) or not isinstance(native.get("agents"), list):
            raise GrokBotError("Grok returned an invalid native roster.")
        known = {row.get("id"): index for index, row in enumerate(rows)}
        native_ids = set()
        for raw in native["agents"]:
            if raw.get("harness") != "temporal":
                continue
            normalized = _temporal_agent(raw)
            native_ids.add(normalized["id"])
            index = known.get(normalized["id"])
            if index is None:
                known[normalized["id"]] = len(rows)
                rows.append(normalized)
            else:
                # A gateway cache may still contain a reseller who left. The
                # control-plane room membership wins; live runtime flags stay.
                rows[index] = {**rows[index], **normalized}
        if strict_native:
            rows = [row for row in rows if row.get("harness") != "temporal" or row.get("id") in native_ids]
    except (GrokBotError, OSError, ValueError, AttributeError):
        if strict_native:
            raise GrokBotError("Native group membership could not be read. Retry inspection before changing members.") from None
        pass  # The computer roster remains authoritative for older clients.
    return rows


def _members(agents: list[dict[str, Any]], ids: list[str], *, minimum=2) -> list[dict[str, Any]]:
    if not isinstance(ids, list) or not minimum <= len(ids) <= 6 or not all(isinstance(value, str) and value for value in ids) or len(set(ids)) != len(ids):
        raise GrokBotError(f"A group needs {minimum}–6 distinct bot IDs.")
    found = {agent.get("id"): agent for agent in agents}
    result = []
    for agent_id in ids:
        agent = found.get(agent_id)
        if not agent or agent.get("isGroup") is True:
            raise GrokBotError("Every group member must be an existing individual bot.")
        _box_owned(agent)
        result.append(agent)
    return result


def _matching_group(agents, name, ids):
    matches = [a for a in agents if str(a.get("name") or "").strip().casefold() == name.casefold()]
    if len(matches) > 1:
        raise GrokBotError("Multiple conversations have that name. Choose a unique group name.")
    if not matches:
        return None
    group = matches[0]
    reported = group.get("memberIds")
    if group.get("isGroup") is not True or not isinstance(reported, list) or len(reported) != len(ids) or not all(isinstance(item, str) for item in reported) or set(reported) != set(ids):
        raise GrokBotError("That name belongs to a different conversation or member set. Choose another name.")
    _box_owned(group)
    return group


def _create_group(box, *, name, description, ids, reuse=False, access=None):
    name = _name(name, "Group name")
    agents = _setup_roster(box, access)
    members = _members(agents, ids)
    harnesses = {_harness(member) for member in members}
    if len(harnesses) != 1:
        raise GrokBotError("Choose members on the same Grok runtime; mixed-runtime groups are not supported by this CLI.")
    existing = _matching_group(agents, name, ids)
    if existing:
        if not reuse:
            raise GrokBotError("That group already exists. Pass --reuse to open it without sending a message.")
        return {"group": _summarize_agent(existing, full=True), "members": [_summarize_agent(a) for a in members], "created": False}
    # Verified against the native app's createGroup dispatch. Box groups have
    # no verified idempotency nonce: send once, then reconcile the roster.
    try:
        if "temporal" in harnesses:
            if not access:
                raise GrokBotError("A signed-in native Grok session is required.")
            nonce = str(uuid.uuid4())
            created = _connect(access, "aiserver.v1.GrokBotService", "CreateGrokBotRoom", {
                "agentId": nonce, "name": name, "description": description, "memberAgentIds": ids, "humanMemberUserIds": []})
            candidate = _temporal_agent(created.get("agent"))
            if candidate["id"] != nonce or candidate["isGroup"] is not True:
                raise GrokBotError("Native group creation returned an unexpected identity.")
        else:
            created = _gateway(box, "/api/createGroup", {"name": name, "description": description, "memberAgentIds": ids})
            candidate = _extract_agent(created)
    except (GrokBotError, OSError, ValueError):
        try:
            recovered = _matching_group(_setup_roster(box, access), name, ids)
        except (GrokBotError, OSError, ValueError):
            recovered = None
        if recovered:
            return {"group": _summarize_agent(recovered, full=True), "members": [_summarize_agent(a) for a in members], "created": True}
        raise GrokBotError("Group creation could not be confirmed. Inspect Grok Bot before retrying; this command did not resend the request.") from None
    fresh = _matching_group(_setup_roster(box, access), name, ids)
    if not fresh or fresh.get("id") != candidate.get("id"):
        raise GrokBotError("Group creation could not be verified in the roster. Inspect Grok Bot before retrying.")
    return {"group": _summarize_agent(fresh, full=True), "members": [_summarize_agent(a) for a in members], "created": True}


def cmd_group_create(args):
    with _setup_lock():
        access, box = _session()
        agents = _setup_roster(box, access)
        ids = list(args.member_id or [])
        for name in args.member_name or []:
            matches = [a for a in agents if str(a.get("name") or "").casefold() == name.casefold()]
            if len(matches) != 1:
                raise GrokBotError(f"Member name {name!r} must match exactly one bot; use --member-id.")
            ids.append(matches[0]["id"])
        result = _create_group(box, name=args.name, description=args.description, ids=ids, reuse=args.reuse, access=access)
        print(json.dumps(result, indent=2))


def _group_from_roster(agents, *, agent_id=None, name=None):
    if agent_id:
        found = [a for a in agents if a.get("id") == agent_id]
    else:
        found = [a for a in agents if str(a.get("name") or "").casefold() == str(name or "").casefold()]
    if len(found) != 1 or found[0].get("isGroup") is not True:
        raise GrokBotError("Choose one existing native Grok Bot group.")
    group = found[0]
    _box_owned(group)
    return group


def cmd_group_info(args):
    access, box = _session()
    agents = _setup_roster(box, access)
    group = _group_from_roster(agents, agent_id=args.id, name=args.name)
    if _harness(group) == "temporal":
        group = {**group, "isRunning": _native_running(access, group["id"])}
    members = _members(agents, group.get("memberIds") or [], minimum=1)
    print(json.dumps({"group": _summarize_agent(group, full=True), "members": [_summarize_agent(a) for a in members]}, indent=2))


def _remove_group_members(access, box, *, agent_id=None, name=None, member_ids):
    if not isinstance(member_ids, list) or not member_ids or not all(isinstance(value, str) and value for value in member_ids) or len(set(member_ids)) != len(member_ids):
        raise GrokBotError("Pass distinct existing bot IDs to remove.")
    agents = _setup_roster(box, access)
    group = _group_from_roster(agents, agent_id=agent_id, name=name)
    native = _harness(group) == "temporal"
    if native:
        # A mutation must never use only the computer's cached member set.
        agents = _setup_roster(box, access, strict_native=True)
        group = _group_from_roster(agents, agent_id=group["id"])
    members = _members(agents, group.get("memberIds"), minimum=1)
    _members(agents, member_ids, minimum=1)
    before = [member["id"] for member in members]
    removed = [value for value in member_ids if value in before]
    absent = [value for value in member_ids if value not in before]
    remaining = [value for value in before if value not in removed]
    if not remaining:
        raise GrokBotError("A group must keep at least one bot. The last member cannot leave.")

    def matches_remaining(current):
        values = current.get("memberIds")
        return (current.get("id") == group["id"] and current.get("isGroup") is True
                and isinstance(values, list) and len(values) == len(remaining)
                and all(isinstance(value, str) for value in values)
                and set(values) == set(remaining))

    def result(current, current_agents):
        current_members = _members(current_agents, current.get("memberIds"), minimum=1)
        return {"group": _summarize_agent(current, full=True),
                "members": [_summarize_agent(member) for member in current_members],
                "removed": bool(removed), "removedMemberIds": removed,
                "alreadyAbsentMemberIds": absent}

    if not removed:
        return result(group, agents)
    # Verified against 0.59.1: the native UI sets the remaining membership;
    # this never deletes bots, messages, or the room itself. Send only once.
    try:
        if native:
            response = _connect(access, "aiserver.v1.GrokBotService", "SetGrokBotRoomMembers",
                                {"agentId": group["id"], "memberAgentIds": remaining})
            updated = _temporal_agent(response.get("agent"))
        else:
            response = _gateway(box, "/api/setGroupMembers", {"id": group["id"], "memberAgentIds": remaining})
            updated = _extract_agent(response)
        if not matches_remaining(updated):
            raise GrokBotError("The server did not confirm the requested group membership.")
    except (GrokBotError, OSError, ValueError, AttributeError):
        # Reconcile only. Replaying a stale replacement list could restore a
        # participant independently removed by another client.
        try:
            fresh_agents = _setup_roster(box, access, strict_native=native)
            fresh = _group_from_roster(fresh_agents, agent_id=group["id"])
            if matches_remaining(fresh):
                return result(fresh, fresh_agents)
        except (GrokBotError, OSError, ValueError, AttributeError):
            pass
        raise GrokBotError("Group member removal could not be confirmed. Inspect the group before retrying; the request was not resent.") from None
    # The mutation response is the authoritative native room snapshot. Do not
    # replace it with a lagging gateway record immediately after the write.
    return result(updated, agents)


def cmd_group_remove_member(args):
    with _setup_lock():
        access, box = _session()
        result = _remove_group_members(access, box, agent_id=args.id, name=args.name, member_ids=args.member_id)
        print(json.dumps(result, indent=2))


DEMO_DEFAULTS = {
    "product": "Harbor & Co. unworn selvedge denim trucker jackets, made in Porto",
    "quantity": 300, "askingPricePence": 3800, "floorPricePence": 2200,
    "denimBudgetPence": 1080000, "bargainBudgetPence": 840000,
    "premiumBudgetPence": 552000, "premiumMaxQuantity": 120,
}
DEMO_ROLES = [
    ("merchant", "Last Drop Merchant", "Merchant · Last Drop", "Sell the lot, compare whole-lot and compatible split offers by recovery, verify arithmetic and floor, then recommend an allocation."),
    ("denim", "Denim Dan", "Denim specialist · Last Drop", "You are an independent denim reseller. Value selvedge quality and prefer whole lots. Choose offers dynamically within the round budget."),
    ("bargain", "Bargain Bea", "Value reseller · Last Drop", "You are an independent value reseller. Prioritize low cost and quick sell-through. Counter or walk away to protect your margin."),
    ("premium", "Premium Priya", "Premium curator · Last Drop", "You are an independent premium curator. Prefer a small quality selection and consider compatible split offers."),
]


def _demo_config(raw):
    try:
        provided = json.loads(raw)
    except (ValueError, TypeError):
        raise GrokBotError("Demo config must be a JSON object.") from None
    if not isinstance(provided, dict) or set(provided) - set(DEMO_DEFAULTS):
        raise GrokBotError("Demo config contains unsupported fields.")
    config = {**DEMO_DEFAULTS, **provided}
    if not isinstance(config["product"], str) or not config["product"].strip() or len(config["product"]) > 500:
        raise GrokBotError("Product must contain 1–500 characters.")
    config["product"] = config["product"].strip()
    for key, value in config.items():
        if key == "product":
            continue
        limit = 10000 if key in ("quantity", "premiumMaxQuantity") else 1000000000
        if type(value) is not int or not 1 <= value <= limit:
            raise GrokBotError(f"{key} must be a positive integer no greater than {limit}.")
    if config["floorPricePence"] > config["askingPricePence"]:
        raise GrokBotError("The floor cannot exceed the asking price.")
    if config["premiumMaxQuantity"] > config["quantity"]:
        raise GrokBotError("Premium quantity cannot exceed available stock.")
    return config


def _demo_brief(config):
    money = lambda key: f"GBP {config[key] / 100:.2f}"
    return (
        "Start a NEW independent fictional Last Drop demo round. Earlier offers and approvals do not apply.\n"
        f"Lot: {config['quantity']} units of {config['product']}. Asking {money('askingPricePence')} per unit; firm floor {money('floorPricePence')} per unit.\n"
        f"Denim Dan prefers the whole lot; total budget {money('denimBudgetPence')}. "
        f"Bargain Bea prioritizes low cost; total budget {money('bargainBudgetPence')}. "
        f"Premium Priya values quality; at most {config['premiumMaxQuantity']} units, total budget {money('premiumBudgetPence')}.\n"
        "Merchant: introduce the stock and invite all three buyers. Buyers: each choose your own price and quantity, explain briefly and react to competing bids. "
        "Use this native group to hand off with mentions; speak only as yourself. Merchant may counter once; buyers may each respond once more. "
        "Compare eligible totals and compatible splits; check every line against the floor, budgets and stock. Recommend an allocation and STOP for fresh human approval. "
        "Keep messages under 60 words. No real orders, payments, inventory changes, external messages or tools. Do not choose a predetermined winner."
    )


def cmd_setup_demo(args):
    name = _name(args.name, "Group name")
    config = _demo_config(args.config_json)
    if name.casefold() in {row[1].casefold() for row in DEMO_ROLES}:
        raise GrokBotError("Group name must differ from the teammate names.")
    with _setup_lock():
        access, box = _session()
        agents = _setup_roster(box, access)
        selected = {}
        # Preflight every role before creating anything. Reused profiles are
        # never rewritten: round-specific constraints belong in the brief.
        for role, bot_name, title, description in DEMO_ROLES:
            matches = [a for a in agents if str(a.get("name") or "").casefold() == bot_name.casefold()]
            if len(matches) > 1 or (matches and matches[0].get("isGroup") is True):
                raise GrokBotError(f"Cannot uniquely reuse {bot_name}. Resolve that name in Grok Bot first.")
            if matches:
                _box_owned(matches[0])
                selected[role] = matches[0]
        same_name = [a for a in agents if str(a.get("name") or "").strip().casefold() == name.casefold()]
        if same_name:
            if len(selected) != 4:
                raise GrokBotError("That room name already exists, but its demo teammates could not all be resolved.")
            _matching_group(agents, name, [selected[row[0]]["id"] for row in DEMO_ROLES])
        existing_harnesses = {_harness(agent) for agent in selected.values()}
        if len(existing_harnesses) > 1:
            raise GrokBotError("The demo teammates use different Grok runtimes. Choose a consistent set before setup.")
        harness = next(iter(existing_harnesses), "temporal")
        created_ids = []
        for role, bot_name, title, description in DEMO_ROLES:
            if role in selected:
                continue
            standing = description + " Fictional negotiation only. Follow the current round's budgets and stock. Two rounds, then stop for human approval. Never place orders, make payments or change inventory."
            try:
                if harness == "temporal":
                    nonce = str(uuid.uuid4())
                    result = _connect(access, "aiserver.v1.GrokBotService", "CreateGrokBotTemporalAgent", {
                        "agentId": nonce, "name": bot_name, "description": standing, "title": title,
                        "avatarShape": "", "avatarColor": "", "harness": "GROK_BOT_AGENT_HARNESS_KIND_TEMPORAL",
                        "kickstartRequested": False, "introductionSuppressed": True})
                    agent = _temporal_agent(result.get("agent"))
                    if agent["id"] != nonce or agent["isGroup"]:
                        raise GrokBotError("New teammate identity could not be verified.")
                else:
                    result = _gateway(box, "/api/createAgent", {"name": bot_name, "description": standing, "origin": "user", "isKickstartRequested": False})
                    agent = _extract_agent(result)
                    if agent.get("name") != bot_name or agent.get("isGroup"):
                        raise GrokBotError("New teammate identity could not be verified.")
            except (GrokBotError, OSError, ValueError):
                raise GrokBotError(f"Creation of {bot_name} is uncertain. Inspect Grok Bot before retrying; setup will reuse unique existing teammates.") from None
            selected[role] = agent
            created_ids.append(agent["id"])
            # Setting a title applies only to a bot created by this invocation.
            if harness == "box":
                _gateway(box, "/api/updateAgent", {"id": agent["id"], "profile": {"name": bot_name, "title": title, "description": standing}})
        ids = [selected[row[0]]["id"] for row in DEMO_ROLES]
        result = _create_group(box, name=name, description="Last Drop fictional merchant/reseller negotiation. Two rounds and human approval; no real commerce.", ids=ids, reuse=True, access=access)
        roles_by_id = {selected[row[0]]["id"]: row[0] for row in DEMO_ROLES}
        print(json.dumps({"group": result["group"], "members": [{**a, "role": roles_by_id[a["id"]]} for a in result["members"]],
                          "created": {"bots": created_ids, "group": result["created"]}, "brief": _demo_brief(config), "config": config}, indent=2))


def _add_agent_selector(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id", help="Grok Bot UUID")
    parser.add_argument("--name", help="Grok Bot display name")


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with Grok Bot and create new teammates.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Check sign-in and cloud computer health")

    list_p = sub.add_parser("list", help="List Grok Bots")
    list_p.add_argument("--full", action="store_true", help="Do not truncate descriptions")

    create_p = sub.add_parser("create", help="Spin up a new Grok Bot")
    create_p.add_argument("--name", required=True)
    create_p.add_argument("--title", default="")
    create_p.add_argument("--description", default="")
    create_p.add_argument("--no-kickstart", action="store_true")
    create_p.add_argument("--force", action="store_true", help="Allow a duplicate name")

    update_p = sub.add_parser("update", help="Edit a Grok Bot profile")
    _add_agent_selector(update_p)
    update_p.add_argument("--rename")
    update_p.add_argument("--title")
    update_p.add_argument("--description")

    send_p = sub.add_parser("send", help="Send a message without waiting")
    _add_agent_selector(send_p)
    send_p.add_argument("--prompt", required=True)

    chat_p = sub.add_parser("chat", help="Send a message and wait for new replies")
    _add_agent_selector(chat_p)
    chat_p.add_argument("--prompt", required=True)
    chat_p.add_argument("--timeout", type=int, default=90)
    chat_p.add_argument("--poll", type=float, default=2.0)

    transcript_p = sub.add_parser("transcript", help="Read a Grok Bot conversation")
    _add_agent_selector(transcript_p)
    transcript_p.add_argument("--limit", type=int, default=20)

    group_p = sub.add_parser("group-create", help="Create a native group with 2–6 existing bots")
    group_p.add_argument("--name", required=True)
    group_p.add_argument("--description", default="")
    group_p.add_argument("--member-id", action="append", help="Repeat for each member")
    group_p.add_argument("--member-name", action="append", help="Repeat for each unique member name")
    group_p.add_argument("--reuse", action="store_true", help="Reuse only an exact name and member-set match")

    group_info_p = sub.add_parser("group-info", help="Inspect and verify a native group and its members")
    _add_agent_selector(group_info_p)

    remove_member_p = sub.add_parser("group-remove-member", help="Remove existing bots from a native group without deleting them; retain at least one member")
    _add_agent_selector(remove_member_p)
    remove_member_p.add_argument("--member-id", action="append", required=True, help="Repeat for each bot to remove")

    demo_p = sub.add_parser("setup-demo", help="Create or reuse the four Last Drop teammates and their group; never sends a prompt")
    demo_p.add_argument("--name", default="The Last Drop — Live Negotiation")
    demo_p.add_argument("--config-json", default="{}", help="Lot, integer pence prices/budgets and quantities as JSON")

    args = parser.parse_args()
    commands = {
        "status": cmd_status,
        "list": cmd_list,
        "create": cmd_create,
        "update": cmd_update,
        "send": cmd_send,
        "chat": cmd_chat,
        "transcript": cmd_transcript,
        "group-create": cmd_group_create,
        "group-info": cmd_group_info,
        "group-remove-member": cmd_group_remove_member,
        "setup-demo": cmd_setup_demo,
    }
    try:
        commands[args.command](args)
    except GrokBotError as exc:
        _die(str(exc))
    except (OSError, ValueError, KeyError):
        _die("Grok Bot request failed. Check sign-in and connectivity. Inspect existing bots before retrying a creation.")


if __name__ == "__main__":
    main()
