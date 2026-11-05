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
