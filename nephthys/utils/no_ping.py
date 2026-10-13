# Write someone's name in a message without pinging them.
#
# The name is written out with one letter swapped for a lookalike from the
# Cyrillic alphabet, so it reads the same but doesn't match their mentions or
# highlight words.
import logging

from slack_sdk.errors import SlackApiError

from nephthys.utils.slack_user import get_user_profile

# Latin letter -> Cyrillic lookalike, in the order we prefer to swap them
LOOKALIKES = {
    "a": "а",
    "A": "А",
    "e": "е",
    "E": "Е",
    "o": "о",
    "O": "О",
    "i": "і",
    "I": "І",
    "c": "с",
    "C": "С",
    "p": "р",
    "P": "Р",
    "s": "ѕ",
    "S": "Ѕ",
    "x": "х",
    "y": "у",
    "j": "ј",
}

# Display names rarely change, so cache them for the life of the process
_name_cache: dict[str, str] = {}


def obfuscate_name(name: str) -> str:
    """Swaps the first letter that has a lookalike, e.g. "daamin" -> "dаamin"."""
    for letter, lookalike in LOOKALIKES.items():
        if letter in name:
            return name.replace(letter, lookalike, 1)
    return name


async def unpinged_name(slack_id: str) -> str:
    """A user's display name, written so it doesn't ping or highlight them"""
    if slack_id not in _name_cache:
        try:
            profile = await get_user_profile(slack_id)
            _name_cache[slack_id] = profile.display_name()
        except (SlackApiError, ValueError) as e:
            logging.warning(f"Failed to get display name for slack_id={slack_id}: {e}")
            return "someone"
    return obfuscate_name(_name_cache[slack_id])
