"""Static content for synthetic data: cities, activities, traps, defect variants, templates.

Category and tag values must come from taxonomy.yaml (enforced by tests).
"""

from __future__ import annotations

from typing import Literal

Method = Literal["rule", "llm"]

CITIES: list[tuple[str, str]] = [
    # EU / Europe
    ("Berlin", "Germany"),
    ("Munich", "Germany"),
    ("Hamburg", "Germany"),
    ("Cologne", "Germany"),
    ("Vienna", "Austria"),
    ("Salzburg", "Austria"),
    ("Zurich", "Switzerland"),
    ("Lucerne", "Switzerland"),
    ("Paris", "France"),
    ("Lyon", "France"),
    ("Nice", "France"),
    ("Barcelona", "Spain"),
    ("Seville", "Spain"),
    ("Madrid", "Spain"),
    ("Lisbon", "Portugal"),
    ("Porto", "Portugal"),
    ("Rome", "Italy"),
    ("Florence", "Italy"),
    ("Venice", "Italy"),
    ("Amsterdam", "Netherlands"),
    ("Prague", "Czechia"),
    ("Budapest", "Hungary"),
    ("Copenhagen", "Denmark"),
    ("Stockholm", "Sweden"),
    ("Edinburgh", "United Kingdom"),
    ("London", "United Kingdom"),
    ("Dublin", "Ireland"),
    ("Athens", "Greece"),
    ("Santorini", "Greece"),
    ("Dubrovnik", "Croatia"),
    ("Kraków", "Poland"),
    # Global
    ("New York", "United States"),
    ("Tokyo", "Japan"),
    ("Cape Town", "South Africa"),
    ("Marrakech", "Morocco"),
    ("Sydney", "Australia"),
    ("Buenos Aires", "Argentina"),
]

# category -> list of (activity idea, suggested tags, involves alcohol)
ACTIVITIES: dict[str, list[tuple[str, list[str], bool]]] = {
    "Tours & Sightseeing": [
        (
            "guided old town walking tour",
            ["Walking tour", "Guided", "History", "Architecture"],
            False,
        ),
        ("bike tour of the city highlights", ["Bike tour", "Guided", "Outdoor"], False),
        ("hop-on hop-off bus tour", ["Bus tour", "Audio guide", "Self-guided"], False),
        ("street art and neighbourhoods tour", ["Walking tour", "Art", "Local culture"], False),
        ("evening legends and ghost walk", ["Walking tour", "Evening", "History"], False),
    ],
    "Museums & Exhibitions": [
        (
            "entry ticket to a major art museum with audio guide",
            ["Art", "Audio guide", "Indoor"],
            False,
        ),
        ("guided tour of a history museum", ["Guided", "History", "Indoor"], False),
        (
            "interactive science museum visit",
            ["Family-friendly", "Kids activities", "Indoor"],
            False,
        ),
        ("architecture and design exhibition", ["Architecture", "Art", "Indoor"], False),
    ],
    "Attractions & Tickets": [
        ("observation deck ticket", ["Architecture", "Photography"], False),
        ("zoo or aquarium day ticket", ["Family-friendly", "Kids activities", "Wildlife"], False),
        ("castle or palace admission", ["History", "Architecture", "Audio guide"], False),
        ("theme park day pass", ["Family-friendly", "Kids activities", "Full-day"], False),
    ],
    "Food & Drink": [
        (
            "food tasting tour through local markets",
            ["Food tasting", "Walking tour", "Local culture"],
            False,
        ),
        ("wine tasting at a local winery", ["Wine", "Food tasting", "Couples"], True),
        ("craft beer tasting tour", ["Beer", "Evening", "Local culture"], True),
        ("traditional cafe and pastry tour", ["Food tasting", "Walking tour", "Half-day"], False),
    ],
    "Outdoor & Adventure": [
        (
            "guided hike in the surrounding hills or mountains",
            ["Hiking", "Nature", "Guided", "Full-day"],
            False,
        ),
        ("climbing or via ferrata introduction", ["Outdoor", "Guided", "Half-day"], False),
        ("wildlife spotting excursion", ["Wildlife", "Nature", "Photography"], False),
        ("e-bike countryside tour", ["Bike tour", "Nature", "Outdoor"], False),
    ],
    "Water Activities": [
        ("kayak tour on a nearby river, lake or coast", ["Kayaking", "Outdoor", "Nature"], False),
        ("sunset boat cruise", ["Boat tour", "Evening", "Couples"], False),
        ("snorkelling trip by boat", ["Snorkelling", "Boat tour", "Wildlife"], False),
        ("stand-up paddle lesson", ["Outdoor", "Half-day"], False),
    ],
    "Day Trips": [
        ("full-day trip to a nearby historic town", ["Full-day", "Bus tour", "History"], False),
        ("day trip to a national park or nature reserve", ["Full-day", "Nature", "Hiking"], False),
        ("three-day regional tour", ["Multi-day", "Guided", "Local culture"], False),
        ("wine region day trip", ["Full-day", "Wine", "Bus tour"], True),
    ],
    "Shows & Performances": [
        ("classical concert in a historic venue", ["Evening", "Local culture", "Indoor"], False),
        ("traditional music and dance show", ["Evening", "Local culture"], False),
        ("musical or theatre ticket", ["Evening", "Indoor"], False),
        ("dinner show", ["Evening", "Food tasting"], False),
    ],
    "Classes & Workshops": [
        (
            "cooking class with a local chef",
            ["Cooking class", "Food tasting", "Local culture"],
            False,
        ),
        ("photography walk workshop", ["Photography", "Walking tour"], False),
        ("pottery or craft workshop", ["Art", "Local culture", "Indoor"], False),
        (
            "pasta or dumpling making class for families",
            ["Cooking class", "Family-friendly"],
            False,
        ),
    ],
    "Nightlife": [
        ("guided pub crawl", ["Beer", "Evening"], True),
        ("rooftop bar and cocktail tour", ["Evening", "Couples"], True),
        ("jazz club night", ["Evening", "Local culture"], False),
    ],
    "Wellness": [
        ("thermal spa day entry", ["Indoor", "Couples"], False),
        ("morning yoga session in a park", ["Outdoor", "Nature"], False),
        ("hammam and massage experience", ["Couples", "Local culture"], False),
    ],
    "Transfers & Transport": [
        ("airport transfer to the city centre", [], False),
        ("private driver for half a day", ["Half-day"], False),
        ("ferry ticket to a nearby island", ["Boat tour"], False),
    ],
}

LENGTHS: dict[str, str] = {
    "short": "description 50-90 words",
    "medium": "description 100-170 words",
    "long": "description 200-320 words, may use 2-4 paragraphs",
}

QUALITIES: dict[str, str] = {
    "polished": "professional copywriter: vivid but precise, well structured",
    "average": "competent but generic marketplace copy: some clichés, slightly uneven structure",
    "plain": (
        "written by a busy supplier: terse, functional, short sentences, few adjectives; "
        "reads a bit flat but is correct and complete"
    ),
}
QUALITY_WEIGHTS: dict[str, float] = {"polished": 0.35, "average": 0.40, "plain": 0.25}

# Attribute keys the generator may use. Boolean keys take "true"/"false".
BOOL_ATTRIBUTES = [
    "skip_the_line",
    "small_group",
    "private",
    "wheelchair_accessible",
    "hotel_pickup",
    "instant_confirmation",
]
STR_ATTRIBUTES = ["max_group_size", "languages", "min_age"]
ATTRIBUTE_KEYS = BOOL_ATTRIBUTES + STR_ATTRIBUTES

TEXT_FIELDS = [
    "title",
    "short_description",
    "description",
    "duration",
    "meeting_point",
    "cancellation_policy",
]
LIST_FIELDS = ["inclusions", "exclusions"]
PRICE_FIELDS = ["title", "short_description", "description"]
REQUIRED_TEXT_FIELDS = [
    "title",
    "short_description",
    "description",
    "duration",
    "meeting_point",
    "cancellation_policy",
]

# --- Traps: near-misses that must NOT be flagged ---------------------------

TRAP_KINDS = [
    "title_acronym_or_registered",
    "fee_in_cancellation",
    "na_in_sentence",
    "locale_variant",
    "short_in_description",
    "street_address_meeting_point",
]

# Allowed-but-not-preferred time variants (taxonomy locale_conventions).
TIME_VARIANTS: dict[str, list[str]] = {"de-DE": ["14 Uhr", "14:00"], "en-GB": ["2pm", "14:00"]}
FEE_FORMATS: dict[str, list[str]] = {
    "de-DE": ["25 €", "25,00 €", "EUR 25"],
    "en-GB": ["€25", "EUR 25"],
}
NA_TOKEN: dict[str, str] = {"de-DE": "n. a.", "en-GB": "N/A"}

# --- Hard cases (tie-break pairs) -> (seeded type, variant) ----------------

HARD_CASES: dict[str, tuple[str, str]] = {
    "D2/D3": ("D2", "claim_vs_attribute"),
    "D2/D4": ("D2", "promised_in_exclusions"),
    "D3/D4": ("D4", "pickup_promised"),
    "D5/D6": ("D6", "price_other_format"),
    "D1/D8": ("D1", "placeholder_punct"),
}

DEFECT_IDS = ["D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8"]

VARIANTS: dict[str, list[tuple[Method, str]]] = {
    "D1": [("rule", "empty"), ("rule", "placeholder"), ("rule", "empty_list"), ("rule", "lorem")],
    "D2": [("llm", "duration_mismatch"), ("llm", "included_but_paid_extra")],
    "D3": [("llm", "unsupported_claim")],
    "D4": [
        ("llm", "food_promised"),
        ("llm", "drink_promised"),
        ("llm", "ticket_promised"),
        ("llm", "transport_promised"),
    ],
    "D5": [
        ("llm", "untranslated_fragment"),
        ("llm", "time_format"),
        ("llm", "date_format"),
        ("llm", "currency_in_cancellation"),
        ("llm", "number_format"),
    ],
    "D6": [
        ("rule", "contact_phone"),
        ("rule", "contact_email"),
        ("rule", "contact_url"),
        ("rule", "contact_handle"),
        ("llm", "promo"),
        ("llm", "price"),
    ],
    "D7": [
        ("rule", "tag_out_of_vocab"),
        ("rule", "category_out_of_vocab"),
        ("rule", "category_mismatch"),
        ("rule", "tag_mismatch"),
    ],
    "D8": [
        ("rule", "caps_title"),
        ("rule", "repeated_punct"),
        ("rule", "emoji_title"),
        ("rule", "long_title"),
        ("rule", "dup_sentence"),
    ],
}
HARD_VARIANT_METHOD: dict[str, Method] = {
    "claim_vs_attribute": "llm",
    "promised_in_exclusions": "llm",
    "pickup_promised": "llm",
    "price_other_format": "llm",
    "placeholder_punct": "rule",
}

# --- Rule-injector templates -------------------------------------------------

PLACEHOLDERS: dict[str, list[str]] = {
    "en-GB": ["TBD", "tbc", "TBC", "N/A", "see above", "xxx", "-", "Tbd."],
    "de-DE": ["folgt", "Folgt", "siehe oben", "n. a.", "TBD", "xxx", "-", "folgt."],
}
PLACEHOLDERS_PUNCT: dict[str, list[str]] = {
    "en-GB": ["TBD!!!", "XXX", "TBC???", "N/A!!!", "tbd?!?", "SEE ABOVE!!!"],
    "de-DE": ["FOLGT!!!", "XXX", "folgt!!!", "TBD!!!", "n. a.!!!", "SIEHE OBEN!!!"],
}
# Placeholders never go in the title. "see above" / "siehe oben" refer to earlier text, so
# they only make sense in fields shown below the description.
PLACEHOLDER_FIELDS = ["short_description", "duration", "meeting_point", "cancellation_policy"]
REFERENCE_PLACEHOLDER_FIELDS = ["duration", "meeting_point", "cancellation_policy"]
REFERENCE_PLACEHOLDERS = {"see above", "siehe oben"}
# Categories where a genuinely trademarked brand/attraction name (®) is plausible.
REGISTERED_OK_CATEGORIES = {
    "Attractions & Tickets",
    "Shows & Performances",
    "Museums & Exhibitions",
    "Tours & Sightseeing",
    "Food & Drink",
}
LOREM = [
    "Lorem ipsum dolor sit amet.",
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
    "lorem ipsum",
    "Lorem Ipsum",
]

# {slug} = ascii city slug. Each entry: (format name, template).
CONTACTS: dict[str, dict[str, list[tuple[str, str]]]] = {
    "en-GB": {
        "contact_phone": [
            ("phone_intl", "+44 20 7946 0958"),
            ("phone_national", "020 7946 0958"),
            ("phone_mobile", "+44 (0)7700 900123"),
        ],
        "contact_email": [
            ("email", "bookings@{slug}-tours.co.uk"),
            ("email_personal", "{slug}.guide@gmail.com"),
        ],
        "contact_url": [
            ("url_www", "www.{slug}tours.com"),
            ("url_https", "https://{slug}-experiences.com/book"),
        ],
        "contact_handle": [
            ("handle_instagram", "@{slug}_tours"),
            ("handle_dotted", "@visit.{slug}"),
        ],
    },
    "de-DE": {
        "contact_phone": [
            ("phone_intl", "+49 30 12345678"),
            ("phone_slash", "030 / 1234 5678"),
            ("phone_mobile", "0171 2345678"),
        ],
        "contact_email": [
            ("email", "info@{slug}-touren.de"),
            ("email_personal", "{slug}.guide@web.de"),
        ],
        "contact_url": [
            ("url_www", "www.{slug}-erleben.de"),
            ("url_https", "https://{slug}-touren.de/buchen"),
        ],
        "contact_handle": [
            ("handle_instagram", "@{slug}_touren"),
            ("handle_dotted", "@erlebe.{slug}"),
        ],
    },
}
CONTACT_PHRASES: dict[str, dict[str, list[str]]] = {
    "en-GB": {
        "contact_phone": [
            "Questions? Call us on {c}.",
            "Running late? Ring {c}.",
            "For group bookings phone {c}.",
        ],
        "contact_email": [
            "For group bookings email {c}.",
            "Send dietary requirements to {c}.",
            "Questions: {c}",
        ],
        "contact_url": [
            "More photos at {c}.",
            "Book direct at {c} for more dates.",
            "Details: {c}",
        ],
        "contact_handle": [
            "Follow us on Instagram: {c}",
            "Tag us {c} in your photos!",
            "Find us at {c}.",
        ],
    },
    "de-DE": {
        "contact_phone": [
            "Fragen? Rufen Sie uns an: {c}.",
            "Bei Verspätung bitte {c} anrufen.",
            "Gruppenbuchungen telefonisch unter {c}.",
        ],
        "contact_email": [
            "Gruppenbuchungen bitte an {c}.",
            "Allergien bitte vorab an {c} melden.",
            "Kontakt: {c}",
        ],
        "contact_url": ["Mehr Fotos unter {c}.", "Weitere Termine direkt auf {c}.", "Infos: {c}"],
        "contact_handle": [
            "Folgen Sie uns auf Instagram: {c}",
            "Markieren Sie uns: {c}",
            "Uns finden Sie unter {c}.",
        ],
    },
}
CONTACT_FIELDS = ["description", "short_description", "meeting_point", "cancellation_policy"]

OUT_OF_VOCAB_TAGS = [
    "Sightseeing",
    "Wine tasting",
    "Kids",
    "Romantic",
    "Adventure",
    "Bootstour",
    "Familie",
    "Must-see",
    "City tour",
    "Foodie",
]
OUT_OF_VOCAB_CATEGORIES = [
    "Tours",
    "Food & Wine",
    "Kultur",
    "Sightseeing Tours",
    "Activities",
    "Adventure Sports",
]
# category -> categories that are clearly wrong for it
CATEGORY_MISMATCH: dict[str, list[str]] = {
    "Tours & Sightseeing": ["Wellness", "Transfers & Transport"],
    "Museums & Exhibitions": ["Water Activities", "Nightlife"],
    "Attractions & Tickets": ["Classes & Workshops", "Wellness"],
    "Food & Drink": ["Water Activities", "Transfers & Transport"],
    "Outdoor & Adventure": ["Museums & Exhibitions", "Nightlife"],
    "Water Activities": ["Museums & Exhibitions", "Classes & Workshops"],
    "Day Trips": ["Wellness", "Nightlife"],
    "Shows & Performances": ["Outdoor & Adventure", "Transfers & Transport"],
    "Classes & Workshops": ["Water Activities", "Transfers & Transport"],
    "Nightlife": ["Museums & Exhibitions", "Wellness"],
    "Wellness": ["Nightlife", "Outdoor & Adventure"],
    "Transfers & Transport": ["Food & Drink", "Shows & Performances"],
}
# category -> tags that are clearly wrong for it (used when the activity has no alcohol)
WRONG_TAGS: dict[str, list[str]] = {
    "Tours & Sightseeing": ["Snorkelling", "Cooking class"],
    "Museums & Exhibitions": ["Kayaking", "Hiking"],
    "Attractions & Tickets": ["Snorkelling", "Cooking class"],
    "Food & Drink": ["Kayaking", "Snorkelling"],
    "Outdoor & Adventure": ["Indoor", "Cooking class"],
    "Water Activities": ["Hiking", "Cooking class"],
    "Day Trips": ["Snorkelling", "Cooking class"],
    "Shows & Performances": ["Kayaking", "Hiking"],
    "Classes & Workshops": ["Snorkelling", "Kayaking"],
    "Nightlife": ["Kids activities", "Hiking"],
    "Wellness": ["Beer", "Kayaking"],
    "Transfers & Transport": ["Cooking class", "Snorkelling"],
}
AUDIENCE_TAGS = ["Kids activities", "Family-friendly"]

EMOJI = ["🚤", "🍷", "✨", "🔥", "😍", "🏰", "🌅", "👉", "⭐", "🎉", "🚲", "🍝"]
REPEATED_PUNCT = ["!!!", "!!!!", "?!?", "!!!!!", "!?!"]
LONG_TITLE_SUFFIXES: dict[str, list[str]] = {
    "en-GB": [
        " – Discover Hidden Corners, Charming Streets and Local Stories Along the Way",
        " | A Memorable Experience for Curious Travellers, Couples, Friends and Families",
        " – See the City From a Completely New Perspective and Hear Its Local Stories",
    ],
    "de-DE": [
        " – Entdecken Sie versteckte Ecken, charmante Gassen und spannende Geschichten",
        " | Ein besonderes Erlebnis für neugierige Reisende, Paare, Freunde und Familien",
        " – Erleben Sie die Stadt aus einer ganz neuen Perspektive und mit allen Sinnen",
    ],
}
