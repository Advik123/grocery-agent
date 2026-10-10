# parser.py
# Validates and cleans the incoming items dict from the POST body.
# Phase 2: the Swift frontend will send items; this parser stays unchanged.
# Phase 3: Siri App Intents will also produce item dicts that flow through here.


def parse_items(data: dict) -> dict[str, int]:
    """Validate and normalise an items mapping.

    Expected shape: {"eggs": 3, "milk": 2}
    - Keys are item names (str).  Stripped of whitespace.
    - Values are quantities (int).  Must be positive integers.

    Raises ValueError on malformed input.
    """
    if not isinstance(data, dict):
        raise ValueError("items must be a JSON object mapping names to quantities")

    cleaned: dict[str, int] = {}

    for name, qty in data.items():
        # --- validate name ---
        if not isinstance(name, str):
            raise ValueError(f"item name must be a string, got {type(name).__name__}")
        name = name.strip()
        if not name:
            raise ValueError("item name cannot be empty or whitespace only")

        # --- validate quantity ---
        if not isinstance(qty, int) or isinstance(qty, bool):
            raise ValueError(
                f"quantity for '{name}' must be a positive integer, got {qty!r}"
            )
        if qty < 1:
            raise ValueError(
                f"quantity for '{name}' must be a positive integer, got {qty}"
            )

        cleaned[name] = qty

    return cleaned
