"""Phonetic name generation for ephemeral config entries.

Format: adjective-noun-noun (e.g., 'quiet-river-stone').
~200×200×200 = 8M combinations. Collision-checked against config + in-memory set.
"""

from __future__ import annotations

import re
import secrets

_ADJECTIVES = [
    "amber", "arctic", "autumn", "azure", "bright", "calm", "cedar", "clear",
    "coral", "cosmic", "crimson", "crystal", "dawn", "deep", "dusk", "ember",
    "emerald", "frozen", "gentle", "golden", "granite", "harbor", "hidden",
    "hollow", "honey", "icy", "indigo", "iron", "ivory", "jade", "jasper",
    "lapis", "lavender", "lazy", "lemon", "lunar", "mango", "marble", "midnight",
    "misty", "mossy", "noble", "northern", "ocean", "olive", "opal", "pacific",
    "pale", "palm", "panda", "peach", "pearl", "penny", "pine", "polar",
    "purple", "quartz", "quiet", "rapid", "restless", "river", "rosy", "ruby",
    "rusty", "sage", "sandy", "scarlet", "sea", "shadow", "silent", "silver",
    "slate", "snowy", "solar", "southern", "spice", "spring", "starry", "steel",
    "stormy", "sunny", "sunset", "swift", "tangerine", "timber", "topaz", "violet",
    "warm", "western", "wild", "willow", "windy", "winter", "wise", "wolf",
    "wooden", "yellow", "young", "zebra", "amber", "ancient", "ashen", "astral",
    "birch", "bitter", "blossom", "blue", "boreal", "brass", "bronze", "butter",
    "cardinal", "cherry", "chestnut", "cinder", "cinnamon", "copper", "cotton",
    "dapper", "desert", "distant", "dolphin", "drift", "dune", "dusky", "ebony",
    "fairy", "fern", "fjord", "flax", "frost", "fuzzy", "garnet", "ginger",
    "glacier", "glossy", "granite", "hazel", "heather", "hickory", "honey",
    "ivory", "jet", "keen", "kelp", "lemon", "lilac", "linden", "magnolia",
    "mahogany", "mauve", "mercury", "milky", "moss", "mountain", "murmur",
    "nectar", "nickel", "nimble", "oak", "obsidian", "ochre", "onyx", "opal",
    "orchid", "pewter", "plum", "prairie", "quartz", "quicksilver", "rain",
    "rattan", "redwood", "reef", "saffron", "sage", "satin", "seal", "shale",
    "silk", "smoky", "snap", "snow", "spruce", "squirrel", "stone", "sugar",
    "sycamore", "tawny", "thistle", "tiger", "turquoise", "umber", "velvet",
    "walnut", "wheat", "whisper", "willow", "wisteria", "yew", "zephyr",
]

_NOUNS = [
    "river", "stone", "forest", "path", "harbor", "meadow", "canyon", "ridge",
    "valley", "cliff", "reef", "glacier", "delta", "summit", "basin", "grove",
    "fjord", "lagoon", "marsh", "dune", "peak", "pass", "bay", "stream",
    "falls", "field", "wood", "hill", "plain", "bluff", "cove", "isle",
    "arch", "bridge", "tower", "mill", "forge", "haven", "anchor", "beacon",
    "compass", "lantern", "mast", "rudder", "sail", "tide", "wave", "wind",
    "cloud", "mist", "frost", "ember", "spark", "flame", "ash", "shadow",
    "thorn", "root", "branch", "leaf", "bark", "seed", "fern", "moss",
    "hawk", "heron", "raven", "finch", "owl", "swan", "falcon", "eagle",
    "fox", "wolf", "bear", "deer", "otter", "lynx", "hare", "seal",
    "dolphin", "whale", "turtle", "ray", "bass", "trout", "pike", "salmon",
    "bee", "moth", "crane", "stork", "ibis", "lark", "wren", "dove",
    "panda", "koala", "lemur", "bison", "caribou", "marmot", "beaver", "badger",
    "cedar", "birch", "maple", "willow", "aspen", "spruce", "juniper", "laurel",
    "coral", "pearl", "amber", "jade", "opal", "agate", "quartz", "garnet",
    "marble", "slate", "granite", "flint", "obsidian", "basalt", "shale", "pumice",
    "aurora", "comet", "meteor", "nebula", "eclipse", "zenith", "orbit", "cosmos",
    "prairie", "savanna", "tundra", "heath", "moor", "wold", "lea", "chase",
    "spring", "well", "pool", "pond", "lake", "brook", "creek", "channel",
    "gate", "wall", "mound", "ring", "circle", "spire", "dome", "vault",
    "echo", "drift", "gust", "breeze", "gale", "storm", "calm", "hush",
    "trail", "track", "route", "way", "lane", "road", "street", "mews",
]

_GENERATED_NAMES: set[str] = set()


class NameCollisionError(ValueError):
    """Generated or user-specified name collides with an existing alias."""


def generate_phonetic(existing: set[str] | None = None) -> str:
    """Generate a unique adjective-noun-noun name.

    Checks against `existing` (config aliases) and an in-memory set of
    names generated this session. Retries up to 10 times before appending
    a digit.
    """
    existing = existing or set()
    for _ in range(10):
        adj = secrets.choice(_ADJECTIVES)
        n1 = secrets.choice(_NOUNS)
        n2 = secrets.choice(_NOUNS)
        name = f"{adj}-{n1}-{n2}"
        if name not in existing and name not in _GENERATED_NAMES:
            _GENERATED_NAMES.add(name)
            return name
    # Fallback: append a digit
    base = f"{secrets.choice(_ADJECTIVES)}-{secrets.choice(_NOUNS)}"
    suffix = 1
    while f"{base}-{suffix}" in existing or f"{base}-{suffix}" in _GENERATED_NAMES:
        suffix += 1
    name = f"{base}-{suffix}"
    _GENERATED_NAMES.add(name)
    return name


_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_RESERVED = {"host", "match", "include", "proxyjump", "hostname", "user", "port", "identityfile"}


def validate_user_name(name: str, existing: set[str]) -> str:
    """Validate a user-specified name. Raises on invalid or colliding names."""
    if not name:
        raise NameCollisionError("Name cannot be empty")
    if len(name) > 63:
        raise NameCollisionError("Name too long (max 63 characters)")
    if not _NAME_RE.match(name):
        raise NameCollisionError(f"Invalid name: '{name}'. Use lowercase letters, digits, and hyphens only.")
    if name.lower() in _RESERVED:
        raise NameCollisionError(f"'{name}' is a reserved SSH config keyword")
    if name in existing:
        raise NameCollisionError(f"'{name}' already exists in ~/.ssh/config")
    return name
