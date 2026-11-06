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
        "Get the read recipe for a saved, user-approved live Crystallize routine by id. "
        "On the current public SDK this returns status 'pending_host_tools', completed false "
        "and a host_tool_plan, not fetched data. Resolve bindings from fresh evidence and make "
        "the listed calls through your ordinary host tools with normal approvals. Inspect actual "
        "results and finish the whole task. Never repeat crystal_run to execute a returned plan."
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
    "description": ("Delegate self-contained, checkable text work in any subject to the configured helper. "
                    "Examples include extraction, classification, calculations, translation, reformatting and drafting. "
                    "It cannot see this chat or use tools. Include complete inputs, check the result, and keep reasoning "
                    "settings as configured. On failure finish the task yourself. Exact call: "
                    'crystal_cheap({"input":"Complete instruction and source text","expected_output_tokens":1200}).'),
    "parameters": {
        "type": "object",
        "properties": {
            "input": {"type": "string", "minLength": 1, "description": "A complete nonempty instruction and source text. Required even with input_files."},
            "input_files": {"type": "array", "maxItems": 8, "items": {"type": "string", "minLength": 1}, "description": "Optional source paths. Current SDK returns a pending read_file plan before any helper call. Use ordinary host tools to read every page, then append complete source text to input and call again without input_files. A complete nonempty instruction remains required."},
            "save_to": {"type": "string", "minLength": 1, "description": "Optional destination. Current SDK retains the paid text output and returns a pending ordinary write_file plan using its owned placeholder; written remains false. Perform and verify that write through your normal tools. Do not repeat the paid handoff. Check completion metadata; some hosts omit stop reasons."},
            "expected_output_tokens": {"type": "integer", "minimum": 1, "maximum": 4000, "description": "Expected deliverable size for cost and timeout estimates."},
            "subtask": {"type": "boolean", "description": "Delegate a complete self-contained text subtask. No global route change and no tools/actions are available to the helper."},
        },
        "required": ["input"],
    },
}
