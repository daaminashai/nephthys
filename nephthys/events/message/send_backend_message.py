from blockkit import Context
from blockkit import Section
from blockkit import Text
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.database.tables import CategoryTag
from nephthys.database.tables import User
from nephthys.utils.env import env

# Section blocks are capped at 3000 characters, so leave room for the quote markers
MAX_RESOLVED_QUESTION_LENGTH = 2500


async def backend_message_blocks(
    author_user_id: str,
    msg_ts: str,
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
    title: str | None,
    description: str,
    stale: bool = False,
) -> list[dict]:
    """Blocks that replace the backend message once its ticket is resolved.

    The thread in the tickets channel is kept, so this turns the top message
    into a summary of the ticket instead of the tag dropdowns.
    """
    thread_url = f"https://hackclub.slack.com/archives/{env.slack_help_channel}/p{msg_ts.replace('.', '')}"
    question = description
    if len(question) > MAX_RESOLVED_QUESTION_LENGTH:
        question = question[:MAX_RESOLVED_QUESTION_LENGTH] + "…"
    quoted_question = "\n".join(f"> {line}" for line in question.splitlines())
    resolved_by = (
        "Closed automatically for being stale"
        if stale
        else f"Resolved by <@{resolver_user_id}>"
    )

    blocks = []
    if title:
        blocks.append(Section(f"*{title}*"))
    blocks.append(Section(quoted_question or "> _(no text)_"))
    blocks.append(
        Context(
            [
                Text(
                    f":white_check_mark: {resolved_by}. Submitted by <@{author_user_id}>. <{thread_url}|View thread>."
                )
            ]
        )
    )
    return [block.build() for block in blocks]


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
    """Send a "backend" message to the tickets channel with ticket details."""

    return await client.chat_postMessage(
        channel=env.slack_ticket_channel,
        text=backend_message_fallback_text(author_user_id, description, reopened_by),
        blocks=await backend_message_blocks(
            author_user_id, msg_ts, past_tickets, current_category_tag_id, reopened_by
        ),
        username=display_name,
        icon_url=profile_pic,
        metadata={
            "event_type": "nephthys_ticket_message",
            "event_payload": {"source_user_id": author_user_id},
        },
        unfurl_links=True,
        unfurl_media=True,
    )
