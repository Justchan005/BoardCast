"""Where a link decoded from the poster's QR code goes. Shared by the API and the eval so the
accuracy numbers measure exactly what production does. Plain code, no AI."""
from __future__ import annotations


def apply_qr_links(item_type: str, data: dict, links: list[str]) -> dict:
    """Events: the QR link is the registration link and beats anything the model guessed
    (a QR on an event poster means "scan to register"). Announcements: it becomes the action
    link when the model found none printed. Achievements never take QR links."""
    if not links or "_error" in data:
        return data
    if item_type == "event":
        data["registration"] = {"required": True, "link": links[0]}
    elif item_type == "announcement" and not data.get("action_link"):
        data["action_link"] = links[0]
    return data
