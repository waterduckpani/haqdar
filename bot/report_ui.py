"""
Interactive inline-keyboard eligibility report for Telegram.

Presentation layer only — consumes the matching layer's output and renders it as a
tappable overview/detail UI. The matching logic is untouched.

Flow:
- build_report(): stores the displayable schemes for a chat and returns the overview
  (HTML text + inline keyboard, one button per scheme).
- handle_callback(): edits the same message in place — scheme button -> detail view,
  "back" -> overview, "full" -> dumps the plain-text report as a new message.

Per-chat state lives in an in-memory dict. If the bot restarts the state is gone, and the
callback handler tells the worker to run the report again.
"""

import html
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Only these likelihood levels are shown, in this order.
DISPLAY_LEVELS = ["likely eligible", "possibly eligible"]
LEVEL_DOT = {"likely eligible": "✅", "possibly eligible": "🟡"}

# chat_id -> {"family_name", "items": [...], "total_checked", "plain_text"}
_REPORTS: dict[int, dict] = {}

_TELEGRAM_LIMIT = 4000


def _esc(text) -> str:
    """HTML-escape any dynamic text going into an HTML-parse-mode message."""
    return html.escape(str(text)) if text is not None else ""


def _one_line(text, limit: int = 180) -> str:
    if not text:
        return ""
    line = " ".join(str(text).split())
    return (line[: limit - 1] + "…") if len(line) > limit else line


def _is_url(value) -> bool:
    return isinstance(value, str) and value.strip().lower().startswith(("http://", "https://"))


# ---------------------------------------------------------------------------
# Building the displayable item list + overview/detail rendering
# ---------------------------------------------------------------------------

def build_report(chat_id, match_data, schemes, family_name, plain_text):
    """
    Store the report for this chat and return (overview_html, InlineKeyboardMarkup).

    Builds a flat, ordered list of displayable schemes (likely first, then possibly),
    each merged with benefit/verification_note from the schemes data.
    """
    by_name = {s.get("scheme_name"): s for s in schemes}
    buckets: dict[str, list[dict]] = {level: [] for level in DISPLAY_LEVELS}
    for m in match_data.get("matches") or []:
        level = (m.get("likelihood") or "").strip().lower()
        if level in buckets:
            buckets[level].append(m)

    items: list[dict] = []
    for level in DISPLAY_LEVELS:
        for m in buckets[level]:
            scheme = by_name.get(m.get("scheme_name"), {})
            items.append(
                {
                    "scheme_name": m.get("scheme_name", "Unknown scheme"),
                    "likelihood": level,
                    "reasoning": (m.get("reasoning") or "").strip(),
                    "missing_info": m.get("missing_info") or [],
                    "source_link": m.get("source_link") or scheme.get("source_link"),
                    "benefit": _one_line(scheme.get("benefits")),
                    "verification_note": (scheme.get("verification_note") or "").strip(),
                }
            )

    _REPORTS[chat_id] = {
        "family_name": family_name,
        "items": items,
        "total_checked": len(schemes),
        "plain_text": plain_text,
    }
    return _overview_text(chat_id), _overview_markup(chat_id)


def _overview_text(chat_id) -> str:
    report = _REPORTS[chat_id]
    items = report["items"]

    # The scheme names live ONLY on the inline keyboard buttons below (grouped likely
    # first, then possibly). The message body is just header + legend + footer, so we
    # never repeat the names as text.
    lines = [
        f"<b>Eligibility report — {_esc(report['family_name'])}</b>",
        "Review these with the family. Tap a scheme for details.",
        "",
        "✅ Likely eligible — meets the main criteria",
        "🟡 Possibly eligible — check the noted detail",
    ]

    if not items:
        lines.append("")
        lines.append(
            "No likely or possibly-eligible schemes were found from the current list. "
            "Please review the family's details and try again."
        )

    lines.append("")
    lines.append(f"<i>Checked {report['total_checked']} scheme(s).</i>")
    return "\n".join(lines)


def _overview_markup(chat_id) -> InlineKeyboardMarkup:
    report = _REPORTS[chat_id]
    rows: list[list[InlineKeyboardButton]] = []
    for idx, it in enumerate(report["items"]):
        label = f"{LEVEL_DOT[it['likelihood']]} {it['scheme_name']}"
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"s:{idx}")])
    rows.append([InlineKeyboardButton("📄 Send full text version", callback_data="full")])
    return InlineKeyboardMarkup(rows)


def _detail_text(item: dict) -> str:
    lines = [f"<b>{_esc(item['scheme_name'])}</b>  ({_esc(item['likelihood'])})"]
    if item["benefit"]:
        lines.append(_esc(item["benefit"]))
    lines.append("")
    if item["reasoning"]:
        lines.append(f"<b>Why:</b> {_esc(item['reasoning'])}")
    if item["missing_info"]:
        joined = ", ".join(map(str, item["missing_info"]))
        lines.append(f"<b>To confirm, also ask:</b> {_esc(joined)}")
    if item["verification_note"]:
        lines.append(f"⚠ <i>{_esc(item['verification_note'])}</i>")
    lines.append("")
    lines.append("<i>These are AI suggestions — verify before applying.</i>")
    return "\n".join(lines)


def _detail_markup(item: dict) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if _is_url(item["source_link"]):
        rows.append([InlineKeyboardButton("🔗 Official page", url=item["source_link"].strip())])
    rows.append([InlineKeyboardButton("⬅ Back to all schemes", callback_data="back")])
    return InlineKeyboardMarkup(rows)


# ---------------------------------------------------------------------------
# Callback handler
# ---------------------------------------------------------------------------

async def _safe_edit(query, text, markup) -> None:
    """Edit a message, ignoring Telegram's 'not modified' complaint on a repeat tap."""
    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=markup,
            disable_web_page_preview=True,
        )
    except BadRequest as exc:
        if "not modified" in str(exc).lower():
            return
        raise


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route taps on the report keyboard, editing the message in place."""
    query = update.callback_query
    await query.answer()

    chat_id = query.message.chat_id
    data = query.data or ""
    report = _REPORTS.get(chat_id)

    if report is None:
        await query.edit_message_text("Please run the report again.")
        return

    if data == "back":
        await _safe_edit(query, _overview_text(chat_id), _overview_markup(chat_id))

    elif data == "full":
        for chunk in _chunk(report["plain_text"]):
            await query.message.reply_text(chunk)

    elif data.startswith("s:"):
        try:
            idx = int(data[2:])
            item = report["items"][idx]
        except (ValueError, IndexError):
            await query.edit_message_text("Please run the report again.")
            return
        await _safe_edit(query, _detail_text(item), _detail_markup(item))


def _chunk(text: str, limit: int = _TELEGRAM_LIMIT) -> list[str]:
    """Split plain text into Telegram-sized chunks on line breaks."""
    chunks: list[str] = []
    current = ""
    for line in (text or "").split("\n"):
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = line if not current else f"{current}\n{line}"
    if current:
        chunks.append(current)
    return chunks or ["(empty report)"]
