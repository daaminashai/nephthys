import logging

from nephthys.database.enums import TicketStatus
from nephthys.database.enums import UserType
from nephthys.database.tables import Ticket
from nephthys.events.message.send_backend_message import question_preview
from nephthys.utils.env import env
from nephthys.utils.logging import send_heartbeat
from nephthys.utils.ticket_methods import get_backend_message_link

MAX_TICKETS = 25


def link_label(text: str) -> str:
    """Escapes text so it can go inside a Slack <url|label> link"""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "/")
    )


def pending_tickets_message(tickets: list[Ticket]) -> str:
    if not tickets:
        return ":taiyaki: no pending tickets right now, nice work!"

    count = len(tickets)
    lines = [f":taiyaki: *{count} pending ticket{'' if count == 1 else 's'}*"]
    for ticket in tickets[:MAX_TICKETS]:
        label = (
            ticket.title
            or question_preview(ticket.description)
            or f"Ticket #{ticket.id}"
        )
        opened = f"<!date^{int(ticket.created_at.timestamp())}^opened {{ago}}|opened {ticket.created_at:%Y-%m-%d}>"
        waiting_on = (
            "waiting on the asker"
            if ticket.last_msg_by == UserType.HELPER
            else "waiting on a helper"
        )
        lines.append(
            f"• <{get_backend_message_link(ticket)}|{link_label(label)}> - {opened}, {waiting_on}"
        )
    if count > MAX_TICKETS:
        lines.append(f"_(plus {count - MAX_TICKETS} more)_")
    return "\n".join(lines)


async def send_pending_tickets():
    """
    Posts a list of every ticket that's still open to the team channel.
    This task is intended to be run once a day.
    """
    try:
        tickets = (
            await Ticket.objects()
            .where(Ticket.status != TicketStatus.CLOSED)
            .order_by(Ticket.created_at)
        )
        await env.slack_client.chat_postMessage(
            channel=env.slack_bts_channel,
            text=pending_tickets_message(tickets),
            unfurl_links=False,
        )
        logging.info("Pending tickets message sent successfully.")

    except Exception as e:
        logging.error(f"Failed to send pending tickets: {e}", exc_info=True)
        await send_heartbeat("Failed to send pending tickets", messages=[str(e)])
