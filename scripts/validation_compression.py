#!/usr/bin/env python3
"""Live check that Crystallize Chat survives Hermes's context compression.

A chat turns Crystallize on, reads a large result, removes it with Context Rent, then compresses.
Compression gives the chat a new session id. The check is that the new session still has
Crystallize on, still has the result removed, and the AI can still read the original back.
Paid calls happen only with --run.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import tempfile
from pathlib import Path

HOME = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def ask(args, prompt, session=None):
    cmd = ["hermes", "chat", "-Q", "--oneshot", "-m", args.model, "--provider", args.provider, "--reasoning", args.reasoning,
           "-t", "terminal,crystallize", "-q", prompt] + (["--resume", session] if session else [])
    done = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    found = re.search(r"session_id: (\S+)", done.stdout + done.stderr)
    return (found.group(1) if found else session), done.stdout.split("session_id:")[0].strip()


def children(session):
    conn = sqlite3.connect(f"file:{HOME}/state.db?mode=ro", uri=True)
    try:
        return [r[0] for r in conn.execute("SELECT id FROM sessions WHERE parent_session_id = ? AND end_reason IS NULL OR "
                                           "parent_session_id = ? ORDER BY started_at", (session, session))]
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--model", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--reasoning", default="medium")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    if Path(args.report).exists():
        raise SystemExit("Use a new report file; existing evidence is never overwritten.")
    if not args.run:
        print("Add --run to make paid calls.")
        return
    from crystallize import chat_savings, lineage, rent, store

    result = {"schema": 1, "model": args.model, "provider": args.provider}
    with tempfile.TemporaryDirectory(prefix="crystal-compress-") as folder:
        big = Path(folder) / "notes.txt"
        big.write_text("MARKER-PAPAYA\n" + "\n".join(f"filler line {i} about the quarterly plan" for i in range(1500)))
        session, _ = ask(args, "Reply with the single word READY.")
        store.set_chat_enabled(session, True)
        ask(args, f"Run `cat {big}` with the terminal tool and reply with only the word READ.", session)
        for i in range(4):
            ask(args, f"Give a one-sentence tip about planning, number {i + 1}.", session)
        items = rent.report(session)["items"]
        removed = rent.evict(session, items[0]["id"])["ok"] if items else False
        result_id = store.evictions(session).get(items[0]["id"]) if items else None
        _, compress_out = ask(args, "/compress", session)
        result["compress_output"] = compress_out[-300:]
        kids = children(session)
        result["parent"] = session
        result["new_sessions"] = kids
        # This Hermes compresses a manual /compress in place; automatic compression can start a new session.
        child = kids[-1] if kids else session
        result["compressed_in_place"] = not kids
        lineage.forget()
        result["checks"] = {
            "removed_before_compression": removed,
            "compressed": "Compressed:" in compress_out,
            "chat_still_on": bool(child) and store.chat_enabled(child),
            "still_removed": bool(child) and bool(store.evictions(child)),
            "original_readable": bool(child and result_id) and chat_savings._load_full(result_id, child) is not None,
        }
        if child:
            # Compression can summarise the removal note away, so the AI is given the id here, as the panel shows it.
            _, answer = ask(args, f"Use crystal_full_result (find it with tool_search if needed) with result_id {result_id}, "
                                  "then reply with only the word after MARKER-.", child)
            result["read_back_answer"] = answer[-200:]
            result["checks"]["ai_read_it_back"] = "PAPAYA" in answer.upper()
    Path(args.report).write_text(json.dumps(result, indent=1))
    print(json.dumps(result["checks"], indent=1))


if __name__ == "__main__":
    main()
