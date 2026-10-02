"""Omnichannel Inbound Triage Engine for J.A.R.V.I.S. — MARK VIII.

Unified multi-channel message ingestion (Gmail, WhatsApp, Notifications), intelligent
priority scoring (0.0 to 1.0 across Critical, Routine, Spam), automated candidate response
drafting in J.A.R.V.I.S. persona, and morning briefing summarization.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import brain
from status_registry import EvidenceLevel, SubsystemState, get_registry

log = logging.getLogger("jarvis.inbound_triage")

BASE_DIR = Path(__file__).parent.resolve()
DATA_DIR = BASE_DIR / "data"
DRAFTS_FILE = DATA_DIR / "pending_drafts.json"
INBOUND_LOG_FILE = DATA_DIR / "inbound_messages.json"

_lock = threading.RLock()


@dataclass
class InboundMessage:
    msg_id: str
    source: str  # "email", "whatsapp", "system_notification", "sms"
    sender: str
    subject: str
    body: str
    timestamp: float
    priority_score: float  # 0.0 to 1.0
    tier: str  # "CRITICAL", "ROUTINE", "SPAM"
    summary: str
    draft_reply: Optional[str] = None
    processed: bool = False


class InboundTriageManager:
    """Omnichannel message triage and intelligent response drafter."""

    def __init__(self, drafts_path: Optional[Path] = None):
        self.drafts_path = Path(drafts_path).resolve() if drafts_path else DRAFTS_FILE
        self.drafts_path.parent.mkdir(parents=True, exist_ok=True)
        self._messages: List[InboundMessage] = []
        self._drafts: Dict[str, Dict[str, Any]] = {}
        self._load_drafts()

    def _load_drafts(self) -> None:
        with _lock:
            if self.drafts_path.exists():
                try:
                    with open(self.drafts_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            self._drafts = data
                except Exception as exc:
                    log.warning(f"[InboundTriage] Failed reading drafts: {exc}")
                    self._drafts = {}
            else:
                self._drafts = {}

    def _save_drafts(self) -> None:
        with _lock:
            tmp_path = self.drafts_path.with_suffix(".tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(self._drafts, f, indent=2, ensure_ascii=False)
                os.replace(tmp_path, self.drafts_path)
            except Exception as exc:
                log.error(f"[InboundTriage] Failed saving drafts: {exc}")

    def score_priority(self, sender: str, subject: str, body: str) -> Tuple[float, str]:
        """Compute message priority score (0.0 to 1.0) and assign tier."""
        text = f"{sender} {subject} {body}".lower()

        # Spam detection
        spam_patterns = [
            r"\bunsubscribe\b",
            r"\bclick here to claim\b",
            r"\bfree gift\b",
            r"\bviagra\b",
            r"\bcasino\b",
            r"\bexclusive discount\b",
            r"\bwin \$\d+\b",
        ]
        for pat in spam_patterns:
            if re.search(pat, text):
                return 0.1, "SPAM"

        # Critical urgency patterns
        critical_patterns = [
            r"\burgent\b",
            r"\basap\b",
            r"\bemergency\b",
            r"\bsecurity alert\b",
            r"\bexam schedule\b",
            r"\binterview\b",
            r"\bpayment required\b",
            r"\bdeadline today\b",
            r"\bserver down\b",
            r"\bproduction outage\b",
        ]
        for pat in critical_patterns:
            if re.search(pat, text):
                return 0.95, "CRITICAL"

        # Work / personal / routine
        if len(body) > 20 and not re.search(r"\bno-reply\b|\bnewsletter\b", sender.lower()):
            return 0.65, "ROUTINE"

        return 0.4, "ROUTINE"

    def draft_persona_reply(self, message: InboundMessage) -> str:
        """Generate a polite, sharp J.A.R.V.I.S.-style draft reply for the user."""
        if message.tier == "SPAM":
            return ""

        sender_name = message.sender.split("<")[0].strip() or "there"
        if "@" in sender_name:
            sender_name = sender_name.split("@")[0].capitalize()

        if message.tier == "CRITICAL":
            return (
                f"Hello {sender_name},\n\n"
                f"Thank you for your urgent note regarding '{message.subject or 'this matter'}'. "
                f"I am actively prioritizing this and will coordinate the necessary response immediately.\n\n"
                f"Best regards,\nTony Stark (via J.A.R.V.I.S.)"
            )

        return (
            f"Hello {sender_name},\n\n"
            f"Thank you for reaching out regarding '{message.subject or 'your message'}'. "
            f"I have received your note and will review and get back to you shortly.\n\n"
            f"Best regards,\nTony Stark (via J.A.R.V.I.S.)"
        )

    def triage_inbound_message(
        self,
        source: str,
        sender: str,
        subject: str,
        body: str,
        timestamp: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Triage an incoming message, assign priority score, and generate candidate draft."""
        ts = timestamp or time.time()
        msg_id = f"MSG-{int(ts)}-{abs(hash(sender + subject)) % 10000}"

        score, tier = self.score_priority(sender, subject, body)
        summary = body[:120].strip().replace("\n", " ")
        if len(body) > 120:
            summary += "..."

        msg = InboundMessage(
            msg_id=msg_id,
            source=source,
            sender=sender,
            subject=subject,
            body=body,
            timestamp=ts,
            priority_score=score,
            tier=tier,
            summary=summary,
        )

        if tier in ("CRITICAL", "ROUTINE"):
            draft = self.draft_persona_reply(msg)
            msg.draft_reply = draft
            with _lock:
                self._drafts[msg_id] = {
                    "msg_id": msg_id,
                    "source": source,
                    "sender": sender,
                    "subject": subject,
                    "tier": tier,
                    "priority_score": score,
                    "draft_reply": draft,
                    "staged_at": ts,
                    "status": "pending_approval",
                }
                self._save_drafts()

        with _lock:
            self._messages.append(msg)

        log.info(f"[InboundTriage] Triaged {source} message from {sender} -> {tier} (score={score:.2f})")

        try:
            get_registry().set_capability_evidence(
                "INBOUND_TRIAGE",
                EvidenceLevel.LIVE,
                f"Triaged {source} message from {sender} [{tier}]",
                source="inbound_triage.triage_inbound_message",
            )
        except Exception:
            pass

        return asdict(msg)

    def list_pending_drafts(self) -> List[Dict[str, Any]]:
        """Retrieve all currently staged candidate response drafts."""
        with _lock:
            return list(self._drafts.values())

    def approve_draft(self, msg_id: str) -> Dict[str, Any]:
        """Approve and dispatch a staged draft reply."""
        with _lock:
            if msg_id not in self._drafts:
                return {"success": False, "status": "draft_not_found", "message": f"Draft {msg_id} not found."}

            draft = self._drafts[msg_id]
            draft["status"] = "approved"
            draft["approved_at"] = time.time()
            self._save_drafts()

            log.info(f"[InboundTriage] Draft {msg_id} approved for transmission to {draft.get('sender')}")
            return {
                "success": True,
                "status": "dispatched",
                "message": f"Draft {msg_id} approved and marked for dispatch.",
                "draft": draft,
            }

    def get_morning_briefing_summary(self) -> str:
        """Compile an executive spoken morning summary of inbound comms."""
        with _lock:
            messages = list(self._messages)

        if not messages:
            return "Good morning, sir. All communication channels are quiescent with zero urgent unread messages."

        critical = [m for m in messages if m.tier == "CRITICAL"]
        routine = [m for m in messages if m.tier == "ROUTINE"]

        lines = [f"Good morning, sir. Inbound communication triage report:"]
        if critical:
            lines.append(f"You have {len(critical)} critical priority item(s):")
            for c in critical[:3]:
                lines.append(f"- From {c.sender}: '{c.subject}' ({c.summary})")
        else:
            lines.append("No critical emergency alerts.")

        if routine:
            lines.append(f"{len(routine)} routine correspondence items pending review.")

        return "\n".join(lines)


_triage_instance: Optional[InboundTriageManager] = None


def get_inbound_triage() -> InboundTriageManager:
    global _triage_instance
    if _triage_instance is None:
        with _lock:
            if _triage_instance is None:
                _triage_instance = InboundTriageManager()
    return _triage_instance


def triage_inbound_message(
    source: str,
    sender: str,
    subject: str,
    body: str,
    timestamp: Optional[float] = None,
) -> Dict[str, Any]:
    return get_inbound_triage().triage_inbound_message(source, sender, subject, body, timestamp)


def list_pending_drafts() -> List[Dict[str, Any]]:
    return get_inbound_triage().list_pending_drafts()


def approve_draft(msg_id: str) -> Dict[str, Any]:
    return get_inbound_triage().approve_draft(msg_id)


def get_morning_briefing_summary() -> str:
    return get_inbound_triage().get_morning_briefing_summary()
