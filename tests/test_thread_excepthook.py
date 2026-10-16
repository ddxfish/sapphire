"""core.sapphire_logging routes thread deaths into the log, SystemExit included.
The default hook wrote to stderr only and ignored SystemExit; uvicorn's bind
failure in its daemon thread was exactly that (2026-10-16, row 13)."""
import sys
import types

from core import sapphire_logging as sl


def _args(exc, thread_name='uvicorn'):
    try:
        raise exc
    except BaseException as e:
        return types.SimpleNamespace(exc_type=type(e), exc_value=e, exc_traceback=e.__traceback__,
                                     thread=types.SimpleNamespace(name=thread_name))


def test_system_exit_in_a_thread_is_logged(caplog):
    with caplog.at_level('ERROR', logger='threads'):
        sl._thread_excepthook(_args(SystemExit(1)))
    rec = caplog.records[-1]
    assert "Thread 'uvicorn' called sys.exit(1)" in rec.message and 'the process is not' in rec.message


def test_an_exception_in_a_thread_is_logged_with_its_traceback(caplog):
    with caplog.at_level('ERROR', logger='threads'):
        sl._thread_excepthook(_args(RuntimeError('port taken'), 'worker-3'))
    rec = caplog.records[-1]
    assert "Unhandled exception in thread 'worker-3'" in rec.message
    assert rec.exc_info and rec.exc_info[0] is RuntimeError and 'port taken' in str(rec.exc_info[1])


def test_not_installed_under_pytest_but_installable():
    import threading
    assert threading.excepthook is not sl._thread_excepthook     # tests keep pytest's own catcher
    assert callable(sl._thread_excepthook)
