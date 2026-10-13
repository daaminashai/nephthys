import logging

from blockkit import Context
from blockkit import Section
from blockkit import Text
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.database.tables import CategoryTag
from nephthys.database.tables import User
from nephthys.utils.env import env

QUESTION_PREVIEW_LENGTH = 100


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


async def backend_message_blocks(
    author_user_id: str,
    msg_ts: str,
    description: str,
    past_tickets: int,
    current_category_tag_id: int | None = None,
    reopened_by: User | None = None,
) -> list[dict]:
    thread_url = f"https://hackclub.slack.com/archives/{env.slack_help_channel}/p{msg_ts.replace('.', '')}"
    if current_category_tag_id is not None:
        options = [
            {
                "text": {
                    "type": "plain_text",
                    "text": tag.name,
                },
                "value": f"{tag.id}",
            }
            for tag in await CategoryTag.objects()
        ]
        initial_option = [
            option
            for option in options
            if option["value"] == f"{current_category_tag_id}"
        ][0]
    else:
        initial_option = None
    category_tags_dropdown = {
        "type": "input",
        "label": {"type": "plain_text", "text": "Category tag", "emoji": True},
        "element": {
            "type": "external_select",
            "action_id": "category-tag-list",
            "placeholder": {
                "type": "plain_text",
                "text": "Which category tag fits?",
            },
            "min_query_length": 0,
        },
    }
    if initial_option:
        category_tags_dropdown["element"]["initial_option"] = initial_option

    return [
        Section(question_preview(description) or "_(no text)_").build(),
        category_tags_dropdown,
        {
            "type": "input",
            "label": {"type": "plain_text", "text": "Team tags", "emoji": True},
            "element": {
                "action_id": "team-tag-list",
                "type": "multi_external_select",
                "placeholder": {"type": "plain_text", "text": "Select tags"},
                "min_query_length": 0,
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"Reopened by <@{reopened_by.slack_id}>. Originally submitted by <@{author_user_id}>. <{thread_url}|View thread>."
                        if reopened_by
                        else f"Submitted by <@{author_user_id}>. They have {past_tickets} past tickets. <{thread_url}|View thread>."
                    ),
                }
            ],
        },
    ]


def resolved_backend_message_blocks(
    author_user_id: str,
    resolver_user_id: str,
    msg_ts: str,
    summary: str,
    stale: bool = False,
) -> list[dict]:
    """Blocks that replace the backend message once its ticket is resolved.

    The thread in the tickets channel is kept (the full question is its first
    reply), so the top message just becomes a short summary of the ticket.
    """
    thread_url = f"https://hackclub.slack.com/archives/{env.slack_help_channel}/p{msg_ts.replace('.', '')}"
    resolved_by = (
        "Closed automatically for being stale"
        if stale
        else f"Resolved by <@{resolver_user_id}>"
    )
    return [
        Section(f"*{summary}*").build(),
        Context(
            [
                Text(
                    f":white_check_mark: {resolved_by}. Submitted by <@{author_user_id}>. <{thread_url}|View thread>."
                )
            ]
        ).build(),
    ]


def backend_message_fallback_text(
    author_user_id: str,
    description: str,
    reopened_by: User | None = None,
) -> str:
    return (
        f"Reopened ticket from <@{author_user_id}>: {description}"
        if reopened_by
        else f"New question from <@{author_user_id}>: {description}"
    )


async def send_backend_message(
    author_user_id: str,
    msg_ts: str,
    description: str,
    past_tickets: int,
    client: AsyncWebClient,
    current_category_tag_id: int | None = None,
    reopened_by: User | None = None,
    display_name: str | None = None,
    profile_pic: str | None = None,
):
    """Send a "backend" message to the tickets channel with ticket details.

    The message itself only shows a preview of the question, so the full
    question is posted as the first reply in its thread."""

    response = await client.chat_postMessage(
        channel=env.slack_ticket_channel,
        text=backend_message_fallback_text(author_user_id, description, reopened_by),
        blocks=await backend_message_blocks(
            author_user_id=author_user_id,
            msg_ts=msg_ts,
            description=description,
            past_tickets=past_tickets,
            current_category_tag_id=current_category_tag_id,
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
                text=description,
                username=display_name,
                icon_url=profile_pic,
            )
        except SlackApiError as e:
            logging.error(
                f"Failed to post original question in ticket thread ticket_ts={ticket_ts}: {e.response['error']}"
            )
    return response
