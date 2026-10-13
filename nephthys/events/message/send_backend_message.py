import logging

from blockkit import Context
from blockkit import Section
from blockkit import Text
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.database.tables import User
from nephthys.utils.env import env
from nephthys.utils.no_ping import strip_pings
from nephthys.utils.no_ping import unpinged_name

QUESTION_PREVIEW_LENGTH = 100
# Marks the "reopened" replies broadcast to the tickets channel, so they can be
# found and deleted once the ticket is resolved again
REOPEN_MESSAGE_EVENT_TYPE = "nephthys_ticket_reopened"


def question_preview(description: str) -> str:
    """A one-line preview of the question, cut to QUESTION_PREVIEW_LENGTH characters"""
    preview = " ".join(description.split())
    if len(preview) <= QUESTION_PREVIEW_LENGTH:
        return preview
    preview = preview[:QUESTION_PREVIEW_LENGTH]
    # Don't cut a Slack mention or link like <@U123> in half
    if preview.rfind("<") > preview.rfind(">"):
        preview = preview[: preview.rfind("<")]
    return preview.rstrip() + "…"


def help_thread_url(msg_ts: str) -> str:
    return f"https://hackclub.slack.com/archives/{env.slack_help_channel}/p{msg_ts.replace('.', '')}"


# Names in the tickets channel are written with `unpinged_name` rather than as
# <@mentions>, so the helpers in it don't get pinged by every ticket


async def backend_message_blocks(
    author_user_id: str,
    msg_ts: str,
    description: str,
    past_tickets: int,
    reopened_by: User | None = None,
) -> list[dict]:
    thread_url = help_thread_url(msg_ts)
    author = await unpinged_name(author_user_id)
    preview = question_preview(await strip_pings(description))
    return [
        Section(preview or "_(no text)_").build(),
        Context(
            [
                Text(
                    f"Reopened by {await unpinged_name(reopened_by.slack_id)}. Originally submitted by {author}. <{thread_url}|View thread>."
                    if reopened_by
                    else f"Submitted by {author}. They have {past_tickets} past tickets. <{thread_url}|View thread>."
                )
            ]
        ).build(),
    ]


def resolved_backend_message_blocks(msg_ts: str, summary: str) -> list[dict]:
    """Blocks that replace the backend message once its ticket is resolved.

    The thread in the tickets channel is kept (the full question is its first
    reply and who resolved it is its last), so the top message just becomes a
    short summary of the ticket.
    """
    return [
        Section(f"*Summary:* {summary}").build(),
        Context(
            [
                Text(
                    f":white_check_mark: Resolved. <{help_thread_url(msg_ts)}|View thread>."
                )
            ]
        ).build(),
    ]


async def resolved_thread_message_text(
    author_user_id: str, resolver_user_id: str, stale: bool = False
) -> str:
    """The reply posted at the end of the ticket's thread when it's resolved"""
    resolved_by = (
        "Closed automatically for being stale"
        if stale
        else f"Resolved by {await unpinged_name(resolver_user_id)}"
    )
    return f":white_check_mark: {resolved_by}. Submitted by {await unpinged_name(author_user_id)}."


async def backend_message_fallback_text(
    author_user_id: str,
    description: str,
    reopened_by: User | None = None,
) -> str:
    author = await unpinged_name(author_user_id)
    description = await strip_pings(description)
    return (
        f"Reopened ticket from {author}: {description}"
        if reopened_by
        else f"New question from {author}: {description}"
    )


async def send_backend_message(
    author_user_id: str,
    msg_ts: str,
    description: str,
    past_tickets: int,
    client: AsyncWebClient,
    reopened_by: User | None = None,
    display_name: str | None = None,
    profile_pic: str | None = None,
):
    """Send a "backend" message to the tickets channel with ticket details.

    The message itself only shows a preview of the question, so the full
    question is posted as the first reply in its thread."""

    response = await client.chat_postMessage(
        channel=env.slack_ticket_channel,
        text=await backend_message_fallback_text(
            author_user_id, description, reopened_by
        ),
        blocks=await backend_message_blocks(
            author_user_id=author_user_id,
            msg_ts=msg_ts,
            description=description,
            past_tickets=past_tickets,
            reopened_by=reopened_by,
        ),
        username=display_name,
        icon_url=profile_pic,
        metadata={
            "event_type": "nephthys_ticket_message",
            "event_payload": {"source_user_id": author_user_id},
        },
        # The full question is in the thread, so don't unfurl the "View thread" link
        unfurl_links=False,
        unfurl_media=False,
    )
    if description and (ticket_ts := response.get("ts")):
        try:
            await client.chat_postMessage(
                channel=env.slack_ticket_channel,
                thread_ts=ticket_ts,
                text=f"*Original message:*\n{await strip_pings(description)}",
                username=display_name,
                icon_url=profile_pic,
            )
        except SlackApiError as e:
            logging.error(
                f"Failed to post original question in ticket thread ticket_ts={ticket_ts}: {e.response['error']}"
            )
    return response


async def post_reopen_message(
    client: AsyncWebClient,
    ticket_ts: str,
    msg_ts: str,
    description: str,
    reopened_by: User,
):
    """Posts a reply in the ticket's existing thread, also sent to the channel,
    so helpers notice the reopened ticket without starting a new thread.

    That reply is deleted when the ticket is resolved again, so a plain
    "reopened by" reply is posted below it to keep a record in the thread."""
    reopener = await unpinged_name(reopened_by.slack_id)
    preview = question_preview(await strip_pings(description))
    await client.chat_postMessage(
        channel=env.slack_ticket_channel,
        thread_ts=ticket_ts,
        reply_broadcast=True,
        text=f":repeat: Reopened by {reopener}: {preview} <{help_thread_url(msg_ts)}|View thread>",
        metadata={
            "event_type": REOPEN_MESSAGE_EVENT_TYPE,
            "event_payload": {"msg_ts": msg_ts},
        },
        unfurl_links=False,
        unfurl_media=False,
    )
    await client.chat_postMessage(
        channel=env.slack_ticket_channel,
        thread_ts=ticket_ts,
        text=f":repeat: Reopened by {reopener}.",
    )


async def delete_reopen_messages(client: AsyncWebClient, ticket_ts: str):
    """Deletes the "reopened" replies posted by `post_reopen_message` in a ticket's thread"""
    response = await client.conversations_replies(
        channel=env.slack_ticket_channel,
        ts=ticket_ts,
        include_all_metadata=True,
        limit=200,
    )
    for message in response.get("messages", []):
        if message.get("metadata", {}).get("event_type") == REOPEN_MESSAGE_EVENT_TYPE:
            await client.chat_delete(channel=env.slack_ticket_channel, ts=message["ts"])
