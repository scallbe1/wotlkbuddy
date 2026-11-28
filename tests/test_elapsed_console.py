import io
from wotlkbuddy.live.console_log import elapsed_console, log_console


def test_elapsed_console_session_and_rollover():
    clock=[1000.]
    out=io.StringIO()
    with elapsed_console(now=lambda:clock[0]):
        log_console('Starting',stream=out)
        clock[0]+=124*60+15.5
        log_console('Quest complete',stream=out)
        clock[0]=1000+125*60-.01
        log_console('Error: test',stream=out)
    log_console('Unscoped',stream=out)
    assert out.getvalue().splitlines()==[
        '[+ 0m00.0s] Starting','[+ 124m15.5s] Quest complete',
        '[+ 125m00.0s] Error: test','Unscoped']


def test_elapsed_multiline_diagnostics():
    out=io.StringIO()
    with elapsed_console(now=lambda:0.):
        log_console('first\nsecond',stream=out)
    assert out.getvalue()=='[+ 0m00.0s] first\n[+ 0m00.0s] second\n'
