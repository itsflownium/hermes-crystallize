"""Chat analysis: exact repeats and patterns."""

from crystallize import chat


def _c(tool, **args):
    return {"tool": tool, "args": args}


def test_exact_repeated_sequence_is_found():
    deploy = [_c("terminal", command="git pull"), _c("terminal", command="npm run build"),
              _c("terminal", command="rsync dist/ server:")]
    calls = deploy + [_c("read_file", path="a.py")] + deploy + deploy
    found = chat.find_repeats(calls)
    assert found and found[0]["length"] == 3 and found[0]["times"] == 3
    assert found[0]["calls_saved"] == 6


def test_streak_pattern_with_changing_inputs():
    calls = [_c("browser_exec", code=f"step {i}") for i in range(6)]
    assert chat.find_repeats(calls) == []
    patterns = chat.find_patterns(calls)
    assert patterns[0]["kind"] == "streak" and patterns[0]["times"] == 6


def test_side_effect_repeat_is_flagged():
    calls = [_c("send_message", to="me", text="hi")] * 3
    found = chat.find_repeats(calls)
    assert found and found[0]["has_side_effects"]
