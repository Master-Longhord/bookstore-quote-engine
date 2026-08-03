"""WhatsApp Business Cloud API adapter (Meta Graph API v21.0).

Two responsibilities, deliberately kept in one small module because they
share auth and the Graph base URL:
- WhatsAppClient: outbound messages (implements the MessagingClient port)
- parse_webhook + fetch_media: turn Meta's webhook payload into our
  channel-agnostic IncomingMessage

Docs: https://developers.facebook.com/docs/whatsapp/cloud-api
"""
from __future__ import annotations

import logging
from typing import Iterator, Optional

import httpx

from app.domain.models import IncomingMessage, MediaKind

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v21.0"

_MIME_TO_KIND = {
    "image/jpeg": MediaKind.IMAGE,
    "image/png": MediaKind.IMAGE,
    "image/webp": MediaKind.IMAGE,
    "application/pdf": MediaKind.PDF,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": MediaKind.DOCX,
    "application/msword": MediaKind.DOCX,  # treated as docx; python-docx may reject legacy .doc
}


class WhatsAppClient:
    """Outbound side. Implements MessagingClient."""

    def __init__(self, access_token: str, phone_number_id: str, timeout: float = 30.0):
        self._token = access_token
        self._phone_id = phone_number_id
        self._http = httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {access_token}"},
        )

    def send_text(self, to: str, body: str) -> None:
        # WhatsApp hard limit is 4096 chars per text message; split politely.
        for chunk in _chunk(body, 4000):
            resp = self._http.post(
                f"{GRAPH}/{self._phone_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": chunk},
                },
            )
            if resp.status_code >= 400:
                log.error("WhatsApp send failed %s: %s", resp.status_code, resp.text)
                resp.raise_for_status()

    def send_template(
        self,
        to: str,
        template_name: str,
        lang: str = "en_US",
        variables: list[str] | None = None,
    ) -> None:
        """Business-initiated message. Outside WhatsApp's 24-hour customer
        service window you can ONLY send pre-approved template messages —
        create one in Meta Business Manager first.
        """
        payload = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": lang},
            },
        }

        if variables:
            payload["template"]["components"] = [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": val} for val in variables],
                }
            ]

        resp = self._http.post(
            f"{GRAPH}/{self._phone_id}/messages",
            json=payload,
        )
        if resp.status_code >= 400:
            log.error("Template send failed %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()

    def send_document(self, to: str, document_bytes: bytes, filename: str) -> None:
        """Uploads a document to Meta, then sends it to the user."""
        
        # Step 1: Upload the media
        upload_url = f"{GRAPH}/{self._phone_id}/media"
        
        # We need a new httpx client without the default JSON headers for the multipart upload
        upload_http = httpx.Client(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self._token}"}
        )
        
        # Meta requires multipart/form-data for uploads
        files = {
            "file": (filename, document_bytes, "application/pdf")
        }
        data = {
            "messaging_product": "whatsapp"
        }
        
        upload_resp = upload_http.post(upload_url, data=data, files=files)
        if upload_resp.status_code >= 400:
            log.error("Media upload failed %s: %s", upload_resp.status_code, upload_resp.text)
            upload_resp.raise_for_status()
            
        media_id = upload_resp.json().get("id")
        
        if not media_id:
            raise ValueError("Failed to get media_id from Meta")

        # Step 2: Send the document message
        message_url = f"{GRAPH}/{self._phone_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "document",
            "document": {
                "id": media_id,
                "filename": filename
            }
        }
        
        send_resp = self._http.post(message_url, json=payload)
        if send_resp.status_code >= 400:
            log.error("Document send failed %s: %s", send_resp.status_code, send_resp.text)
            send_resp.raise_for_status()

    # ---- inbound media ----

    def fetch_media(self, media_id: str) -> tuple[bytes, str]:
        """Two-step download: get a signed URL, then fetch the bytes."""
        meta = self._http.get(f"{GRAPH}/{media_id}")
        meta.raise_for_status()
        info = meta.json()
        blob = self._http.get(info["url"])
        blob.raise_for_status()
        return blob.content, info.get("mime_type", "application/octet-stream")


def parse_webhook(payload: dict) -> Iterator[dict]:
    """Yield raw message dicts (plus contact name) from a webhook POST.

    Meta batches: entry[] -> changes[] -> value{messages[], contacts[]}.
    """
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            names = {
                c.get("wa_id"): c.get("profile", {}).get("name", "")
                for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                msg["_sender_name"] = names.get(msg.get("from"), "")
                yield msg


def to_incoming(msg: dict, client: WhatsAppClient) -> Optional[IncomingMessage]:
    """Convert one raw WhatsApp message into an IncomingMessage.

    Returns None for message types we don't handle (audio, stickers,
    reactions, statuses...) so the caller can politely decline.
    """
    sender = msg.get("from", "")
    name = msg.get("_sender_name", "")
    mtype = msg.get("type")
    mid = msg.get("id", "")

    if mtype == "text":
        return IncomingMessage(
            sender=sender,
            sender_name=name,
            kind=MediaKind.TEXT,
            text=msg["text"]["body"],
            channel_message_id=mid,
        )

    if mtype in ("image", "document"):
        media = msg[mtype]
        data, mime = client.fetch_media(media["id"])
        kind = _MIME_TO_KIND.get(mime, MediaKind.UNSUPPORTED)
        if kind == MediaKind.UNSUPPORTED:
            return IncomingMessage(
                sender=sender, sender_name=name, kind=MediaKind.UNSUPPORTED,
                text=media.get("caption"), channel_message_id=mid,
            )
        return IncomingMessage(
            sender=sender,
            sender_name=name,
            kind=kind,
            text=media.get("caption"),
            media_bytes=data,
            media_mime=mime,
            channel_message_id=mid,
        )

    return None


def _chunk(text: str, size: int) -> Iterator[str]:
    if len(text) <= size:
        yield text
        return
    lines = text.split("\n")
    buf: list[str] = []
    length = 0
    for line in lines:
        if length + len(line) + 1 > size and buf:
            yield "\n".join(buf)
            buf, length = [], 0
        buf.append(line)
        length += len(line) + 1
    if buf:
        yield "\n".join(buf)