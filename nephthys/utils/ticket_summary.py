import logging

from openai import OpenAIError
from openai.types.chat import ChatCompletion
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from nephthys.utils.ai import ai_client
from nephthys.utils.env import env
from nephthys.utils.logging import send_heartbeat

# Keep the prompt small - long threads are cut down to their most recent messages
MAX_THREAD_MESSAGES = 60
MAX_MESSAGE_LENGTH = 800


async def get_thread_transcript(
    client: AsyncWebClient, channel: str, ts: str, author_slack_id: str
) -> str:
    """Returns the human messages in a thread as "Asker: ..." / "Helper: ..." lines"""
    response = await client.conversations_replies(channel=channel, ts=ts, limit=200)
    lines = []
    for message in response.get("messages", [])[-MAX_THREAD_MESSAGES:]:
        # Skip the bot's own messages (welcome, resolve, backend message, etc)
        if "bot_id" in message or not message.get("text"):
            continue
        speaker = "Asker" if message.get("user") == author_slack_id else "Helper"
        lines.append(f"{speaker}: {message['text'][:MAX_MESSAGE_LENGTH]}")
    return "\n".join(lines)


async def generate_ticket_summary(
    client: AsyncWebClient, msg_ts: str, ticket_ts: str, author_slack_id: str
) -> str | None:
    """Summarises what a resolved ticket concluded, using both the public help
    thread and the helpers' discussion thread in the tickets channel.

    Returns None if AI isn't configured or anything goes wrong."""
    if not ai_client:
        return None

    try:
        help_thread = await get_thread_transcript(
            client, env.slack_help_channel, msg_ts, author_slack_id
        )
        discussion_thread = await get_thread_transcript(
            client, env.slack_ticket_channel, ticket_ts, author_slack_id
        )
    except SlackApiError as e:
        logging.error(
            f"Failed to fetch threads for ticket summary msg_ts={msg_ts}: {e.response['error']}"
        )
        return None

    model = env.ai_title_model
    try:
        response: ChatCompletion = await ai_client.chat.completions.create(
            model=model,
            reasoning_effort="low",
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a helpful assistant that helps organise tickets for Hack Club's support team. "
                        "You're going to read a resolved support ticket: the public help thread, and the helpers' internal discussion about it. "
                        "Write the conclusion that was reached - the answer given or the decision made - not the topic of the question. "
                        "If nothing was concluded, say what's still pending. "
                        "Use no more than 12 words, but as few as possible. "
                        "Return nothing else. Use sentence case, no quote marks, no trailing full stop, and don't name people. "
                        "Be aware, you cannot see uploaded attachments."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Public help thread:\n{help_thread or '(empty)'}\n\n"
                        f"Internal helper discussion:\n{discussion_thread or '(none)'}\n\n"
                        "What was the conclusion?"
                    ),
                },
            ],
        )
    except OpenAIError as e:
        await send_heartbeat(f"Failed to get AI response for ticket summary: {e}")
        return None

    if not (len(response.choices) and response.choices[0].message.content):
        await send_heartbeat(f"AI ticket summary is missing content: {response}")
        return None

    summary = response.choices[0].message.content.strip().strip('"').rstrip(".")
    # Capitalise first letter
    summary = summary[0].upper() + summary[1:] if len(summary) > 1 else summary.upper()
    return summary
