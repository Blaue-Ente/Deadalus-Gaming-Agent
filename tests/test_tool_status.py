"""Native tool outcome parsing — no substring false positives on ERROR in payloads."""

from daedalus.tool_status import native_tool_result_ok


def test_error_in_content_does_not_fail_ok_status():
    txt = (
        "── write_file OK demo/src/constants.js (120 chars) ──\n"
        "const ERROR_COLOR = 0xff0000;\n"
    )
    assert native_tool_result_ok(txt) is True


def test_failed_status_line_fails():
    txt = (
        "── write_file FAILED demo/index.html ──\n"
        "permission denied\n"
    )
    assert native_tool_result_ok(txt) is False


def test_blocked_passes():
    assert native_tool_result_ok("── run_command BLOCKED ──\n", allow_blocked_pass=True) is True
