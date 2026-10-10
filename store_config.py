# store_config.py
# Maps store names to their OurGroceries list URLs.
# Phase 3: multi-store parallel requests will iterate over this map concurrently.
# Phase 3: ngrok tunneling will dynamically rewrite URLs if needed.

STORE_LISTS: dict[str, str] = {
    "aldi": "https://www.ourgroceries.com/your-lists/list/MadL3iwy0vlLuXW1NAypeh",
    "costco": "https://www.ourgroceries.com/your-lists/list/BYNQWCxf0DeKv0ET09k5r0",
    "walmart": "https://www.ourgroceries.com/your-lists/list/Fq98Vqn10PGLcgdOZF5NDU",
    "woodman": "https://www.ourgroceries.com/your-lists/list/M6gpwuGHU9aKZEDmKxAuRx",
    "freshfarm": "https://www.ourgroceries.com/your-lists/list/M6gpwuGHU9aKZEDmKxAuRx",
}


def get_list_url(store: str) -> str:
    """Return the OurGroceries list URL for the given store name.

    Raises KeyError if the store is not registered.
    """
    try:
        return STORE_LISTS[store]
    except KeyError:
        raise KeyError(f"unknown store: {store}")
