"""Tool schemas: what the model reads to decide when to call Crystallize's tools."""

CRYSTAL_LIST = {
    "name": "crystal_list",
    "description": (
        "List the routines Crystallize has saved for this user, with their status "
        "(spotted, testing or live) and number of steps. Use this to find a routine id "
        "before calling crystal_run."
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}

CRYSTAL_RUN = {
    "name": "crystal_run",
    "description": (
        "Run a saved, user-approved Crystallize routine by id. It performs the routine's "
        "fixed steps without further reasoning and returns their combined output, which you "
        "then summarize or act on. Only routines with status 'live' can run."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "routine_id": {
                "type": "string",
                "description": "The routine id from crystal_list.",
            },
        },
        "required": ["routine_id"],
    },
}


CRYSTAL_FULL_RESULT = {
    "name": "crystal_full_result",
    "description": ("Read the exact original of a tool result that Crystallize Chat condensed. Use it when the "
                    "summary leaves out something you need."),
    "parameters": {
        "type": "object",
        "properties": {
            "result_id": {"type": "string", "description": "The full_result_id from the condensed result."},
            "offset": {"type": "integer", "description": "Where to continue reading, from next_offset."},
            "question": {"type": "string", "description": ("Ask a cheap model one question about the result instead "
                                                           "of reading it all. Best for finding a single detail.")},
        },
        "required": ["result_id"],
    },
}


CRYSTAL_CHEAP = {
    "name": "crystal_cheap",
    "description": ("Send one input to a cheaper model and get its output straight back. For routine work such as "
                    "summarizing, extracting data, reformatting, translating or simple drafting. It can't see this "
                    "chat or use tools, so include everything it needs, and check what comes back."),
    "parameters": {
        "type": "object",
        "properties": {"input": {"type": "string", "description": "The full instruction and any text it should work on."}},
        "required": ["input"],
    },
}
