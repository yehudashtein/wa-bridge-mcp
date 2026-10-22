"""
whatsapp.py — Python MCP <-> whatsapp-bridge-node bindings (HTTP-only).

Every operation goes through the bridge's REST API (/v1/*), so the MCP is
portable — point it at the cloud (https://api.api-supp.com/v1, the default) or a
local bridge (http://localhost:8081/v1) by setting WHATSAPP_BRIDGE_URL. Auth is a
tenant bearer token in WHATSAPP_BRIDGE_TOKEN.
"""

from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional, List, Tuple
from urllib.parse import urlparse
import mimetypes
import os
import os.path
import requests
import json
import audio


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

TENANT_TOKEN = os.environ.get("WHATSAPP_BRIDGE_TOKEN", "").strip()
BRIDGE_BASE_URL = os.environ.get(
    "WHATSAPP_BRIDGE_URL", "https://api.api-supp.com/v1"
).rstrip("/")
DEFAULT_TIMEOUT = float(os.environ.get("WHATSAPP_HTTP_TIMEOUT", "30"))


def _headers() -> dict:
    if not TENANT_TOKEN:
        return {}
    return {"Authorization": f"Bearer {TENANT_TOKEN}"}


def _get(path: str, params: Optional[dict] = None, timeout: Optional[float] = None) -> Tuple[int, object]:
    """Return (status, body). body is parsed JSON or text."""
    try:
        r = requests.get(
            f"{BRIDGE_BASE_URL}{path}",
            params=params or None,
            headers=_headers(),
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text
    except requests.RequestException as e:
        return 0, {"error": str(e)}


def _post(path: str, json_body: Optional[dict] = None, params: Optional[dict] = None,
          timeout: Optional[float] = None) -> Tuple[int, object]:
    try:
        r = requests.post(
            f"{BRIDGE_BASE_URL}{path}",
            json=json_body if json_body is not None else None,
            params=params or None,
            headers=_headers(),
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT,
        )
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, r.text
    except requests.RequestException as e:
        return 0, {"error": str(e)}


def _bridge_is_remote() -> bool:
    """True when the bridge is not on this machine. A remote bridge (e.g. the
    cloud api.api-supp.com instance) cannot read this machine's local filesystem,
    so a local media_path must be uploaded as bytes first and sent by media_id."""
    host = (urlparse(BRIDGE_BASE_URL).hostname or "").lower()
    return host not in ("", "localhost", "127.0.0.1", "::1", "0.0.0.0")


def _upload_media(media_path: str) -> Tuple[bool, str]:
    """Upload a local file's bytes to POST /v1/media so a remote bridge can send
    it. Returns (True, media_id) on success or (False, error_message)."""
    filename = os.path.basename(media_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        with open(media_path, "rb") as f:
            data = f.read()
    except OSError as e:
        return False, f"Could not read media file: {e}"
    try:
        r = requests.post(
            f"{BRIDGE_BASE_URL}/media",
            params={"filename": filename},
            data=data,
            headers={**_headers(), "Content-Type": content_type},
            timeout=120,
        )
    except requests.RequestException as e:
        return False, f"Upload request failed: {e}"
    try:
        body = r.json()
    except Exception:
        body = r.text
    if r.status_code in (200, 201) and isinstance(body, dict) and body.get("media_id"):
        return True, body["media_id"]
    return False, f"Upload failed: HTTP {r.status_code} - {body!r}"


# ─────────────────────────────────────────────────────────────────────────────
# Dataclasses (kept identical to pre-cutover for downstream compatibility)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Message:
    timestamp: datetime
    sender: str
    content: str
    is_from_me: bool
    chat_jid: str
    id: str
    chat_name: Optional[str] = None
    media_type: Optional[str] = None


@dataclass
class Chat:
    jid: str
    name: Optional[str]
    last_message_time: Optional[datetime]
    last_message: Optional[str] = None
    last_sender: Optional[str] = None
    last_is_from_me: Optional[bool] = None

    @property
    def is_group(self) -> bool:
        return self.jid.endswith("@g.us")


@dataclass
class Contact:
    phone_number: str
    name: Optional[str]
    jid: str


@dataclass
class MessageContext:
    message: Message
    before: List[Message]
    after: List[Message]


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _parse_dt(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        # bridge uses "YYYY-MM-DD HH:MM:SS±HH:MM"; datetime.fromisoformat needs T separator
        return datetime.fromisoformat(s.replace(" ", "T", 1)) if isinstance(s, str) else None
    except Exception:
        return None


def _to_bridge_ts(s: Optional[str]) -> Optional[str]:
    """Normalize a caller-supplied ISO-8601 cursor to the bridge's stored
    timestamp format: "YYYY-MM-DD HH:MM:SS[.mmm]+00:00" (UTC, space separator).

    The bridge compares timestamps as strings, and rows are stored in the
    space-separated form. An ISO "…T…Z" cursor sorts ABOVE every same-date
    stored row (' ' < 'T'), so incremental since/before queries silently
    matched nothing. Unparseable input is passed through unchanged."""
    if not s:
        return s
    raw = s.strip()
    try:
        iso = raw.replace("Z", "+00:00").replace(" ", "T", 1)
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    except Exception:
        return raw
    base = f"{dt:%Y-%m-%d %H:%M:%S}"
    if dt.microsecond:
        base += f".{dt.microsecond // 1000:03d}"
    return base + "+00:00"


def _msg_from_dict(d: dict) -> Message:
    return Message(
        timestamp=_parse_dt(d.get("timestamp")) or datetime.fromtimestamp(0),
        sender=d.get("sender") or "",
        content=d.get("content") or "",
        is_from_me=bool(d.get("is_from_me")),
        chat_jid=d.get("chat_jid") or "",
        id=d.get("id") or "",
        chat_name=d.get("chat_name"),
        media_type=d.get("media_type") or None,
    )


def _chat_from_dict(d: dict) -> Chat:
    return Chat(
        jid=d.get("jid") or "",
        name=d.get("name"),
        last_message_time=_parse_dt(d.get("last_message_time")),
        last_message=d.get("last_message"),
        last_sender=d.get("last_sender"),
        last_is_from_me=bool(d["last_is_from_me"]) if d.get("last_is_from_me") is not None else None,
    )


def _contact_from_dict(d: dict) -> Contact:
    # Handles both the legacy /contacts/search shape ({phone, name, jid}) and the
    # rich /contacts shape ({jid, pn, name, notify, verified_name, ...}). For the
    # rich shape the phone lives in `pn` and the display name may only be in
    # `notify` (push name) or `verified_name`.
    pn = d.get("pn") or ""
    phone = d.get("phone") or (pn.split("@")[0] if pn else "") or d.get("jid", "").split("@")[0]
    return Contact(
        phone_number=phone,
        name=d.get("name") or d.get("notify") or d.get("verified_name"),
        jid=d.get("jid") or d.get("pn") or "",
    )


# ─────────────────────────────────────────────────────────────────────────────
# LID/PN resolution (now HTTP)
# ─────────────────────────────────────────────────────────────────────────────


def _resolve_phone_to_lid(phone: str) -> Optional[str]:
    if not phone:
        return None
    status, body = _get("/lid/resolve", params={"phone": phone})
    if status == 200 and isinstance(body, dict):
        return body.get("lid")
    return None


def _resolve_lid_to_phone(lid_user: str) -> Optional[str]:
    if not lid_user:
        return None
    status, body = _get("/lid/resolve", params={"lid": lid_user})
    if status == 200 and isinstance(body, dict):
        return body.get("phone")
    return None


def _resolve_chat_jid_alt(chat_jid: str) -> Optional[str]:
    if not chat_jid or "@" not in chat_jid:
        return None
    user, domain = chat_jid.split("@", 1)
    if domain == "s.whatsapp.net":
        lid = _resolve_phone_to_lid(user)
        return f"{lid}@lid" if lid else None
    elif domain == "lid":
        phone = _resolve_lid_to_phone(user)
        return f"{phone}@s.whatsapp.net" if phone else None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Sender-name lookup (kept for format_message compatibility)
# ─────────────────────────────────────────────────────────────────────────────


def get_sender_name(sender_jid: str) -> str:
    if not sender_jid:
        return ""
    # Try exact JID first
    status, body = _get(f"/chats/{requests.utils.quote(sender_jid, safe='')}")
    if status == 200 and isinstance(body, dict) and body.get("name"):
        return body["name"]
    # Fall back to phone-fragment search
    phone_part = sender_jid.split("@")[0] if "@" in sender_jid else sender_jid
    status, body = _get("/chats", params={"q": phone_part, "limit": 1})
    if status == 200 and isinstance(body, dict):
        chats = body.get("chats") or []
        if chats and chats[0].get("name"):
            return chats[0]["name"]
    return sender_jid


def format_message(message: Message, show_chat_info: bool = True) -> str:
    output = ""
    if show_chat_info and message.chat_name:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] Chat: {message.chat_name} "
    else:
        output += f"[{message.timestamp:%Y-%m-%d %H:%M:%S}] "
    content_prefix = ""
    if getattr(message, "media_type", None):
        content_prefix = f"[{message.media_type} - Message ID: {message.id} - Chat JID: {message.chat_jid}] "
    try:
        sender_name = get_sender_name(message.sender) if not message.is_from_me else "Me"
        output += f"From: {sender_name}: {content_prefix}{message.content}\n"
    except Exception as e:
        print(f"Error formatting message: {e}")
    return output


def format_messages_list(messages: List[Message], show_chat_info: bool = True) -> str:
    if not messages:
        return "No messages to display."
    return "".join(format_message(m, show_chat_info) for m in messages)


# ─────────────────────────────────────────────────────────────────────────────
# Read tools (HTTP-backed)
# ─────────────────────────────────────────────────────────────────────────────


def list_messages(
    after: Optional[str] = None,
    before: Optional[str] = None,
    sender_phone_number: Optional[str] = None,
    chat_jid: Optional[str] = None,
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_context: bool = True,
    context_before: int = 1,
    context_after: int = 1,
    include_groups: bool = False,
) -> str:
    """Get messages matching the specified criteria. Returns formatted string."""
    params = {
        "limit": limit,
        "offset": page * limit,
        "include_groups": "true" if include_groups else "false",
    }
    if chat_jid:
        params["chat_jid"] = chat_jid
    if after:
        params["since"] = _to_bridge_ts(after)
    if before:
        params["before"] = _to_bridge_ts(before)
    if query:
        params["q"] = query
    if sender_phone_number:
        # Resolve phone → chat_jid options server-side via direct-chat lookup
        status, body = _get(f"/contacts/{sender_phone_number}/direct-chat")
        if status == 200 and isinstance(body, dict) and body.get("jid"):
            params["chat_jid"] = body["jid"]
        else:
            params["sender"] = sender_phone_number

    status, body = _get("/messages", params=params)
    if status != 200 or not isinstance(body, dict):
        return f"Error fetching messages: HTTP {status} {body!r}"

    msgs = [_msg_from_dict(m) for m in body.get("messages") or []]

    if include_context and msgs:
        out_messages: List[Message] = []
        for m in msgs:
            try:
                ctx = get_message_context(m.id, context_before, context_after)
            except Exception:
                ctx = None
            if ctx:
                out_messages.extend(ctx.before)
                out_messages.append(ctx.message)
                out_messages.extend(ctx.after)
            else:
                out_messages.append(m)
        return format_messages_list(out_messages, show_chat_info=True)

    return format_messages_list(msgs, show_chat_info=True)


def get_message_context(
    message_id: str, before: int = 5, after: int = 5
) -> MessageContext:
    """Get context around a specific message."""
    status, body = _get(f"/messages/{requests.utils.quote(message_id, safe='')}/context",
                        params={"n": max(before, after)})
    if status != 200 or not isinstance(body, dict):
        raise ValueError(f"Message with ID {message_id} not found")

    target = _msg_from_dict(body.get("target") or {})
    bef = [_msg_from_dict(m) for m in (body.get("before") or [])][-before:] if before else []
    aft = [_msg_from_dict(m) for m in (body.get("after") or [])][:after] if after else []
    return MessageContext(message=target, before=bef, after=aft)


def list_chats(
    query: Optional[str] = None,
    limit: int = 20,
    page: int = 0,
    include_last_message: bool = True,
    sort_by: str = "last_active",
) -> List[Chat]:
    params = {
        "limit": limit,
        "offset": page * limit,
        "sort": "name" if sort_by == "name" else "last_active",
    }
    if query:
        params["q"] = query
    status, body = _get("/chats", params=params)
    if status != 200 or not isinstance(body, dict):
        return []
    return [_chat_from_dict(c) for c in body.get("chats") or []]


def search_contacts(query: str) -> List[Contact]:
    if not query:
        return []
    # Use the rich contacts table search (name/notify/verified_name/pn/jid), not
    # the legacy /contacts/search over the sparse chats table.
    status, body = _get("/contacts", params={"q": query, "limit": 50})
    if status != 200 or not isinstance(body, dict):
        return []
    return [_contact_from_dict(c) for c in body.get("contacts") or []]


def get_contact_chats(jid: str, limit: int = 20, page: int = 0) -> List[Chat]:
    status, body = _get(
        f"/contacts/{requests.utils.quote(jid, safe='@')}/chats",
        params={"limit": limit, "offset": page * limit},
    )
    if status != 200 or not isinstance(body, dict):
        return []
    return [_chat_from_dict(c) for c in body.get("chats") or []]


def get_last_interaction(jid: str) -> Optional[str]:
    status, body = _get(f"/contacts/{requests.utils.quote(jid, safe='@')}/last-interaction")
    if status != 200 or not isinstance(body, dict):
        return None
    return format_message(_msg_from_dict(body))


def get_chat(chat_jid: str, include_last_message: bool = True) -> Optional[Chat]:
    status, body = _get(f"/chats/{requests.utils.quote(chat_jid, safe='')}")
    if status != 200 or not isinstance(body, dict):
        return None
    return _chat_from_dict(body)


def get_direct_chat_by_contact(sender_phone_number: str) -> Optional[Chat]:
    status, body = _get(f"/contacts/{sender_phone_number}/direct-chat")
    if status != 200 or not isinstance(body, dict):
        return None
    return _chat_from_dict(body)


# ─────────────────────────────────────────────────────────────────────────────
# Write tools
# ─────────────────────────────────────────────────────────────────────────────


def send_message(recipient: str, message: str) -> Tuple[bool, str, str]:
    """Send a text WhatsApp message. Returns (ok, status_message, wa_message_id)."""
    if not recipient:
        return False, "Recipient must be provided", ""
    status, body = _post("/messages", json_body={"recipient": recipient, "message": message})
    if status == 200 and isinstance(body, dict):
        return (
            bool(body.get("ok")),
            f"Message sent to {body.get('recipient', recipient)}",
            body.get("wa_message_id") or "",
        )
    return False, f"Error: HTTP {status} - {body!r}", ""


def send_file(recipient: str, media_path: str, caption: str = "") -> Tuple[bool, str]:
    if not recipient:
        return False, "Recipient must be provided"
    if not media_path:
        return False, "Media path must be provided"
    if not os.path.isfile(media_path):
        return False, f"Media file not found: {media_path}"

    # A remote bridge can't read this machine's filesystem, so upload the bytes
    # and reference them by media_id. A local bridge reads the path directly.
    if _bridge_is_remote():
        ok, result = _upload_media(media_path)
        if not ok:
            return False, result
        media_ref = {"media_id": result}
    else:
        media_ref = {"media_path": os.path.abspath(media_path)}

    status, body = _post(
        "/messages",
        json_body={
            "recipient": recipient,
            "message": caption,
            **media_ref,
        },
        timeout=120,
    )
    if status == 200 and isinstance(body, dict):
        kind = (body.get("media") or {}).get("kind", "media")
        wa_id = body.get("wa_message_id", "")
        return True, f"{kind} sent to {body.get('recipient', recipient)} (id={wa_id})"
    return False, f"Error: HTTP {status} - {body!r}"


def send_audio_message(recipient: str, media_path: str) -> Tuple[bool, str]:
    if not recipient:
        return False, "Recipient must be provided"
    if not media_path:
        return False, "Media path must be provided"
    if not os.path.isfile(media_path):
        return False, f"Media file not found: {media_path}"
    if not media_path.endswith(".ogg"):
        try:
            media_path = audio.convert_to_opus_ogg_temp(media_path)
        except Exception as e:
            return False, f"Error converting file to opus ogg: {e}"
    return send_file(recipient, media_path)


def download_media(message_id: str, chat_jid: str) -> Optional[str]:
    status, body = _post(
        f"/messages/{requests.utils.quote(message_id, safe='')}/download",
        params={"chat_jid": chat_jid},
        timeout=120,
    )
    if status == 200 and isinstance(body, dict) and body.get("ok"):
        path = body.get("path")
        print(f"Media downloaded successfully: {path}")
        return path
    print(f"Download failed: HTTP {status} - {body!r}")
    return None
