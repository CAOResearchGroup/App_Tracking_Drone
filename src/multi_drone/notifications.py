"""Bounded STATUSTEXT history and MAVLink 2 chunk reassembly."""
from __future__ import annotations

from collections import OrderedDict
from datetime import datetime, timezone


SEVERITIES = ("EMERGENCY", "ALERT", "CRITICAL", "ERROR", "WARNING", "NOTICE", "INFO", "DEBUG")
HISTORY_LIMIT = 100


def add_notification(state, severity, text, now, *, source="STATUSTEXT", complete=True):
    # Strip terminal/Qt control sequences; viewers must render this as plain text.
    text = " ".join("".join(c if c.isprintable() else " " for c in text).split())[:2048]
    if not text:
        return None
    state.status_sequence += 1
    message = {"sequence": state.status_sequence, "system_id": state.system_id,
               "component_id": state.component_id, "severity": severity,
               "level": SEVERITIES[severity] if 0 <= severity < len(SEVERITIES) else "UNKNOWN",
               "text": text, "received_at": now, "utc": datetime.now(timezone.utc).isoformat(),
               "source": source, "complete": complete}
    state.status_messages.append(message)
    del state.status_messages[:-HISTORY_LIMIT]
    if source == "STATUSTEXT":
        state.last_status, state.last_status_at, state.last_status_severity = text, now, severity
    return message


class StatusTextAssembler:
    """Never combine different drones, components or message IDs; expire gaps."""
    def __init__(self):
        self.pending = OrderedDict()

    def expire(self, now):
        expired = []
        for key, item in list(self.pending.items()):
            if now-item["at"] > 2:
                self.pending.pop(key)
                expired.append((key[:2], item["severity"], self._text(item)+" [thiếu đoạn STATUSTEXT]", False))
        return expired

    @staticmethod
    def _text(item):
        return b"".join(item["parts"]).decode("utf-8", errors="replace")

    def feed(self, packet, now):
        data = packet.data
        severity = int(data.get("severity", 6))
        if not 0 <= severity <= 7:
            raise ValueError("Invalid STATUSTEXT severity")
        value = data.get("text", "")
        raw = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(raw) > 50:
            raise ValueError("STATUSTEXT chunk exceeds 50 bytes")
        body = raw.split(b"\0", 1)[0]
        message_id, seq = int(data.get("id", 0)), int(data.get("chunk_seq", 0))
        if not 0 <= message_id <= 65535 or not 0 <= seq <= 255:
            raise ValueError("Invalid STATUSTEXT chunk identity")
        if message_id == 0:
            return [(severity, body.decode("utf-8", errors="replace"), True)]
        key = (packet.system_id, packet.component_id, message_id)
        output = []
        if seq == 0:
            existing = self.pending.get(key)
            if existing and existing["parts"] and existing["parts"][0] == body and existing["severity"] == severity:
                return []  # Duplicate first chunk must not reset the timeout.
            if existing:
                output.append((existing["severity"], self._text(existing)+" [thông báo bị ngắt]", False))
            if len(self.pending) >= 64 and key not in self.pending:
                self.pending.popitem(last=False)
            self.pending[key] = {"parts": [], "severity": severity, "at": now}
        item = self.pending.get(key)
        if item is None:
            return []  # An orphan tail is not a complete diagnostic.
        parts = item["parts"]
        if seq < len(parts) and parts[seq] == body and item["severity"] == severity:
            return output
        if seq != len(parts) or severity != item["severity"]:
            self.pending.pop(key)
            output.append((item["severity"], self._text(item)+" [thiếu/sai thứ tự đoạn STATUSTEXT]", False))
            return output
        parts.append(body)
        # NUL terminates a message; pymavlink may already have stripped padding.
        if len(body) < 50 or len(parts) >= 32:
            complete = len(body) < 50
            text = self._text(item) + ("" if complete else " [vượt giới hạn độ dài]")
            self.pending.pop(key)
            output.append((severity, text, complete))
        return output
