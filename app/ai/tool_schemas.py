from typing import Any

from app.models.domain import UserMode

_ABOUT_FALSA = {
    "type": "function",
    "function": {
        "name": "about_falsa",
        "description": (
            "Retrieve official FALSA company, FAQ, policy, or pricing information."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The customer's question about FALSA.",
                },
                "language": {
                    "type": "string",
                    "enum": ["ar", "en"],
                    "description": "Customer language for the result.",
                },
            },
            "required": ["query", "language"],
            "additionalProperties": False,
        },
    },
}

_SEARCH_TRIPS = {
    "type": "function",
    "function": {
        "name": "search_trips",
        "description": (
            "Search active trips by departure/destination (either can be omitted). "
            "Results sent as WhatsApp cards automatically. "
            "Times Asia/Aden. Buckets: صباح before 12, ظهر 12-17:59, ليل 18+."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "departure": {
                    "type": "string",
                    "description": "Departure city/area in Arabic. Required if destination omitted.",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination city/area in Arabic. Required if departure omitted.",
                },
                "travel_datetime": {
                    "type": "string",
                    "description": "Optional ISO date+time in Asia/Aden; normalized to the matching time bucket.",
                },
                "travel_date": {
                    "type": "string",
                    "description": "Optional trip date YYYY-MM-DD (Asia/Aden).",
                },
                "travel_time": {
                    "type": "string",
                    "enum": ["صباح", "ظهر", "ليل"],
                    "description": "Optional time bucket (Arabic).",
                },
                "travel_time_exact": {
                    "type": "string",
                    "description": "Optional exact time HH:MM (Asia/Aden). Also set the corresponding travel_time bucket.",
                },
                "seats": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional number of seats requested.",
                },
                "vehicle_type": {
                    "type": "string",
                    "description": "Optional car type in Arabic, for example سيارة or باص.",
                },
                "vector_query_text": {
                    "type": "string",
                    "description": "Optional semantic text; auto-built from other fields.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

_CREATE_BOOKING_LEAD = {
    "type": "function",
    "function": {
        "name": "create_booking_lead",
        "description": (
            "Create pending booking (default 1 seat), notify driver. "
            "Call on trip-card reply. Does not reserve or confirm."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "trip_id": {"type": "string", "description": "Trip ID. Optional if replying to a trip card."},
                "requested_seats": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Number of seats requested. Defaults to 1 if not specified.",
                },
                "notes": {
                    "type": "string",
                    "description": "Optional customer notes or pickup details.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

_CREATE_DRIVER_ACCOUNT = {
    "type": "function",
    "function": {
        "name": "create_driver_account",
        "description": "Register this WhatsApp sender as a FALSA driver. Phone from chat session.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Driver full legal name.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}

_CHECK_DRIVER_INFO = {
    "type": "function",
    "function": {
        "name": "check_driver_info",
        "description": "Get driver account, vehicles & active trip summary.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

_CHECK_DRIVER_TRIPS = {
    "type": "function",
    "function": {
        "name": "check_driver_trips",
        "description": "List upcoming active trips (status=active, not departed).",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

_ADD_DRIVER_CAR = {
    "type": "function",
    "function": {
        "name": "add_driver_car",
        "description": "Register vehicle for current driver. Only name required.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Vehicle name or type in Arabic.",
                },
                "plate_number": {
                    "type": "string",
                    "description": "Optional vehicle plate number.",
                },
                "seat_count": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional number of seats in the vehicle.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
}

_ADD_TRIP_BY_DRIVER = {
    "type": "function",
    "function": {
        "name": "add_trip_by_driver",
        "description": (
            "Create a trip for the registered driver. Phone from chat session. "
            "Optional car/seats/price default from latest trip or sole registered vehicle."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "departure": {
                    "type": "string",
                    "description": "Departure city or area in Arabic.",
                },
                "destination": {
                    "type": "string",
                    "description": "Destination city or area in Arabic.",
                },
                "departure_date": {
                    "type": "string",
                    "description": "Trip date as YYYY-MM-DD in Asia/Aden.",
                },
                "departure_time": {
                    "type": "string",
                    "description": "Time bucket: morning/noon/night or صباح/ظهر/ليل.",
                },
                "vehicle_type": {
                    "type": "string",
                    "description": "Optional vehicle name/type in Arabic (e.g. سيارة/باص).",
                },
                "available_seats": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Optional seats available for booking.",
                },
                "total_seats": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Optional total vehicle seats for this trip.",
                },
                "price": {
                    "type": "number",
                    "minimum": 0,
                    "description": "Optional trip price.",
                },
            },
            "required": ["departure", "destination", "departure_date", "departure_time"],
            "additionalProperties": False,
        },
    },
}

_INITIATE_TRIP_ACTION = {
    "type": "function",
    "function": {
        "name": "initiate_trip_action",
        "description": "Start delete/modify flow. Sends numbered trip list.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {
                    "type": "string",
                    "enum": ["DELETE", "MODIFY"],
                    "description": "Whether the driver wants to cancel or edit a trip.",
                },
                "travel_date": {
                    "type": "string",
                    "description": "Optional trip date filter YYYY-MM-DD (Asia/Aden).",
                },
                "travel_time": {
                    "type": "string",
                    "enum": ["صباح", "ظهر", "ليل"],
                    "description": "Departure time bucket in Arabic.",
                },
            },
            "required": ["action_type"],
            "additionalProperties": False,
        },
    },
}

_UPDATE_TRIP_FIELD = {
    "type": "function",
    "function": {
        "name": "update_trip_field",
        "description": "Update one field on the trip the driver is editing. trip_id from active session.",
        "parameters": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "enum": [
                        "departure",
                        "destination",
                        "departure_date",
                        "departure_time",
                        "pickup_time",
                        "vehicle_type",
                        "available_seats",
                        "total_seats",
                        "price",
                    ],
                    "description": "Trip field to update.",
                },
                "value": {
                    "type": "string",
                    "description": "New value. Dates YYYY-MM-DD, time HH:MM, routes Arabic, seats/price digits.",
                },
            },
            "required": ["field", "value"],
            "additionalProperties": False,
        },
    },
}

_DELETE_TRIP_BY_NUMBER = {
    "type": "function",
    "function": {
        "name": "delete_trip_by_number",
        "description": "Cancel a trip by driver-visible number (oldest=1).",
        "parameters": {
            "type": "object",
            "properties": {
                "trip_number": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The one-based number of the trip from the driver trip list.",
                },
            },
            "required": ["trip_number"],
            "additionalProperties": False,
        },
    },
}

_MODIFY_TRIP_BY_NUMBER = {
    "type": "function",
    "function": {
        "name": "modify_trip_by_number",
        "description": "Modify a trip field by driver-visible number (oldest=1).",
        "parameters": {
            "type": "object",
            "properties": {
                "trip_number": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The one-based number of the trip from the driver trip list.",
                },
                "field": {
                    "type": "string",
                    "enum": [
                        "departure",
                        "destination",
                        "departure_date",
                        "departure_time",
                        "pickup_time",
                        "vehicle_type",
                        "available_seats",
                        "total_seats",
                        "price",
                    ],
                    "description": "The trip field to update.",
                },
                "value": {
                    "type": "string",
                    "description": "New value. Dates YYYY-MM-DD, time HH:MM, routes Arabic, seats/price digits.",
                },
            },
            "required": ["trip_number", "field", "value"],
            "additionalProperties": False,
        },
    },
}

_SWITCH_TO_DRIVER = {
    "type": "function",
    "function": {
        "name": "switch_to_driver",
        "description": "Switch sender to driver mode. Requires existing driver account.",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
}

_SWITCH_TO_PASSENGER = {
    "type": "function",
    "function": {
        "name": "switch_to_passenger",
        "description": "Switch sender to passenger mode to search/book trips.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Optional passenger display name.",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
}

_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "about_falsa": _ABOUT_FALSA,
    "search_trips": _SEARCH_TRIPS,
    "create_booking_lead": _CREATE_BOOKING_LEAD,
    "create_driver_account": _CREATE_DRIVER_ACCOUNT,
    "check_driver_info": _CHECK_DRIVER_INFO,
    "check_driver_trips": _CHECK_DRIVER_TRIPS,
    "add_driver_car": _ADD_DRIVER_CAR,
    "add_trip_by_driver": _ADD_TRIP_BY_DRIVER,
    "initiate_trip_action": _INITIATE_TRIP_ACTION,
    "update_trip_field": _UPDATE_TRIP_FIELD,
    "delete_trip_by_number": _DELETE_TRIP_BY_NUMBER,
    "modify_trip_by_number": _MODIFY_TRIP_BY_NUMBER,
    "switch_to_driver": _SWITCH_TO_DRIVER,
    "switch_to_passenger": _SWITCH_TO_PASSENGER,
}

_TOOLS_BY_MODE: dict[UserMode, list[str]] = {
    "new_user": [
        "about_falsa",
        "create_driver_account",
        "switch_to_driver",
        "switch_to_passenger",
    ],
    "driver": [
        "about_falsa",
        "check_driver_info",
        "check_driver_trips",
        "add_driver_car",
        "add_trip_by_driver",
        "delete_trip_by_number",
        "modify_trip_by_number",
        "initiate_trip_action",
        "update_trip_field",
        "switch_to_passenger",
    ],
    "passenger": [
        "about_falsa",
        "search_trips",
        "create_booking_lead",
        "create_driver_account",
        "switch_to_driver",
    ],
}


def get_tool_schemas(user_mode: UserMode = "new_user") -> list[dict[str, Any]]:
    return [_TOOL_SCHEMAS[name] for name in _TOOLS_BY_MODE[user_mode]]


def get_all_tool_schemas() -> list[dict[str, Any]]:
    return list(_TOOL_SCHEMAS.values())
