"""/crystallize in the CLI and TUI, and the live Crystallize line posted after each turn."""

from types import SimpleNamespace

import pytest

from crystallize import brake, chat_commands as cc, chat_savings, live_notice, overview, rent, store

SID = "chat-1"
ITEMS = [
    {"id": "call_a", "tool": "web_extract", "what": "https://example.com/report", "tokens": 9000,
     "per_call_usd": 0.0009, "removed": False, "worth_it": True},
    {"id": "call_b", "tool": "read_file", "what": "notes.md", "tokens": 1200,
     "per_call_usd": 0.00012, "removed": True, "worth_it": False},
]


@pytest.fixture
def chat(monkeypatch):
    state = {"enabled": True, "removed": []}
    monkeypatch.setattr(chat_savings, "status_for", lambda sid: {"enabled": state["enabled"], "can_start": True})
    monkeypatch.setattr(chat_savings, "set_enabled", lambda sid, on: state.update(enabled=on) or {"message": f"on={on}"})
    monkeypatch.setattr(chat_savings, "summary", lambda sid: {
        "enabled": state["enabled"], "chat_spent_usd": 0.0123, "main_model": "tencent/hy3",
        "this_chat": {"tokens_removed": 4200, "net_once": 0.0021},
        "projection_pricing": {"model": "tencent/hy3", "input_usd_per_token": 1.32e-7, "output_usd_per_token": 5e-7}})
    monkeypatch.setattr(rent, "report", lambda sid: {
        "items": ITEMS, "tokens": 30000, "per_call_usd": 0.003, "removed_tokens": 1200,
        "saving_per_call_usd": 0.00012, "auto_on": True, "auto": {"removed": 1, "read_backs": 0}})
    monkeypatch.setattr(rent, "evict", lambda sid, cid: state["removed"].append(("remove", cid)))
    monkeypatch.setattr(rent, "restore", lambda sid, cid: state["removed"].append(("restore", cid)))
    monkeypatch.setattr(overview, "build", lambda sid=None: {
        "saved_usd": 0.05, "live_saving_usd_per_week": 0.02,
        "live": [{"name": "status-check", "runs": 6, "cost_now": 0.0009, "saved_usd": 0.05,
                  "skip_fit": {"message": "Its data changed in 1 of its last 4 runs."}}],
        "finished": [{"name": f"old-{i}", "runs": 3, "cost_now": 0.001} for i in range(8)]})
    return state


def test_status_shows_chat_cron_and_brake(chat):
    text = cc.handle("", SID)
    assert "This chat: Crystallize is on." in text
    assert "$0.01 on tencent/hy3" in text
    assert "4.2k tokens" in text
    assert "1 active, 8 finished" in text and "Saved so far: $0.05" in text
    assert "Runaway Brake: off." in text


def test_rent_lists_numbered_results_and_removes_by_number(chat):
    text = cc.handle("rent", SID)
    assert "1. web_extract" in text and "[worth removing]" in text
    assert "2. read_file" in text and "[removed]" in text
    assert "Removed web_extract" in cc.handle("rent remove 1", SID)
    assert "Restored read_file" in cc.handle("rent restore call_b", SID)
    assert chat["removed"] == [("remove", "call_a"), ("restore", "call_b")]
    assert "No result 9" in cc.handle("rent remove 9", SID)


def test_jobs_puts_active_first_and_collapses_finished(chat):
    text = cc.handle("jobs", SID)
    assert "status-check (saving)" in text and "data changed in 1 of its last 4" in text
    assert "old-0" not in text and "8 more (finished)" in text


def test_brake_prices_chat_and_line_toggles(chat):
    assert "Runaway Brake is on" in cc.handle("brake on", SID)
    assert brake.enabled()
    assert "$0.13 in" in cc.handle("prices", SID)
    assert cc.handle("chat off", SID) == "on=False"
    assert "is off" in cc.handle("line off", SID) and not live_notice.enabled()
    assert "will show" in cc.handle("line on", SID) and live_notice.enabled()


def test_unknown_or_failing_commands_never_break_the_chat(chat, monkeypatch):
    assert cc.handle("bogus", SID) == cc.USAGE
    monkeypatch.setattr(overview, "build", lambda sid=None: 1 / 0)
    assert "couldn't read its data" in cc.handle("", SID)


def test_money_keeps_tiny_costs_readable():
    assert cc._money(0.000043) == "$0.000043"
    assert cc._money(-0.0000123) == "-$0.0000123"
    assert cc._money(0.0021) == "$0.0021"
    assert cc._money(1.5) == "$1.50"
    assert cc._money(None) == "unknown"


class FakeCtx:
    def __init__(self, surface):
        self.surface, self.posts, self.edits = surface, [], []

    def session_surface(self):
        return self.surface

    def post_message(self, text):
        self.posts.append(text)
        ctx = self

        class Handle:
            ok = True

            def edit(self, new):
                ctx.edits.append(new)
                return {"ok": True}
        return Handle()


@pytest.fixture(autouse=True)
def fresh_line():
    for sid in (SID, "chat-2"):
        live_notice.forget(sid)
    yield


def test_line_posts_once_then_edits_in_place_in_the_tui(chat, monkeypatch):
    ctx = FakeCtx("tui")
    hook = live_notice.make_post_llm_call(ctx)
    hook(session_id=SID, platform="tui")
    assert ctx.posts == ["◆ −1.2k tok/call · /crystallize"]  # the TUI status line is short
    hook(session_id=SID, platform="tui")
    assert ctx.edits == []  # unchanged text is not re-posted
    monkeypatch.setattr(rent, "report", lambda sid: {"items": [], "tokens": 500, "removed_tokens": 3000,
                                                     "saving_per_call_usd": 0.0003})
    hook(session_id=SID, platform="tui")
    assert len(ctx.posts) == 1 and ctx.edits[-1] == "◆ −3.0k tok/call · /crystallize"


def test_line_shows_in_classic_cli_but_not_desktop_or_messaging(chat):
    for surface, expected in (("cli", 1), ("desktop", 0), ("telegram", 0)):
        live_notice.forget(SID)
        ctx = FakeCtx(surface)
        live_notice.make_post_llm_call(ctx)(session_id=SID, platform=surface)
        assert len(ctx.posts) == expected, surface
    assert ctx.posts == [] and "1.2k tokens no longer re-sent" in live_notice.text_for(SID, 1)
    assert "spent $0.01" in live_notice.text_for(SID, 1)


def test_off_chat_gets_one_hint_on_its_second_turn(chat):
    chat["enabled"] = False
    ctx = FakeCtx("cli")
    hook = live_notice.make_post_llm_call(ctx)
    hook(session_id="chat-2", platform="cli")
    assert ctx.posts == []
    hook(session_id="chat-2", platform="cli")
    assert ctx.posts and "/crystallize chat on" in ctx.posts[0]
    hook(session_id="chat-2", platform="cli")
    assert len(ctx.posts) == 1 and ctx.edits == []


def test_line_can_be_turned_off_and_never_raises(chat, monkeypatch):
    live_notice.set_enabled(False)
    ctx = FakeCtx("tui")
    live_notice.make_post_llm_call(ctx)(session_id=SID, platform="tui")
    assert ctx.posts == []
    live_notice.set_enabled(True)
    monkeypatch.setattr(chat_savings, "status_for", lambda sid: 1 / 0)
    live_notice.make_post_llm_call(ctx)(session_id=SID, platform="tui")  # no exception
    assert ctx.posts == []
    live_notice.make_post_llm_call(SimpleNamespace())(session_id=SID, platform="cli")  # host without post_message


def test_chat_default_on_covers_interactive_chats_only(monkeypatch, tmp_path):
    import sqlite3
    from crystallize import history
    db = tmp_path / "hermes" / "state.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sessions (id TEXT, source TEXT)")
    conn.executemany("INSERT INTO sessions VALUES (?, ?)", [("tui-1", "tui"), ("cli-1", "cli"), ("cron-1", "cron"), ("sub-1", "subagent")])
    conn.commit()
    conn.close()
    store._interactive.clear()
    assert not store.chat_enabled("tui-1")
    assert "start every new chat" in cc.handle("chat on", "")
    assert "now start with Crystallize on" in cc.handle("chat default on", "tui-1")
    assert store.chat_enabled("tui-1") and store.chat_enabled("cli-1")
    assert not store.chat_enabled("cron-1") and not store.chat_enabled("sub-1") and not store.chat_enabled("unknown")
    store.set_chat_enabled("cli-1", False)
    assert not store.chat_enabled("cli-1")  # an explicit per-chat choice wins
    cc.handle("chat default off", "")
    assert not store.chat_enabled("tui-1")
