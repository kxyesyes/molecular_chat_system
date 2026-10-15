"""Actual RDKit diagnostics with synthetic messages; no scientific result fixtures."""

import logging
from pathlib import Path
import subprocess
import sys
import textwrap
import threading

import pytest
from rdkit import rdBase

from src.system import rdkit_logging


def fresh_python(script):
    """Every installation test starts with real native sinks, not a warmed router."""
    result = subprocess.run([sys.executable, "-"], input=textwrap.dedent(script),
                            text=True, capture_output=True, timeout=30,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_cold_rdkit_import_preserves_existing_logger_configuration():
    fresh_python('''
        import logging
        import sys
        from src.system import rdkit_logging as privacy
        assert 'rdkit' not in sys.modules
        messages = []
        class Capture(logging.Handler):
            def emit(self, record):
                messages.append(record.getMessage())
        logger = logging.getLogger('rdkit')
        handler = Capture()
        existing_filter = logging.Filter()
        logger.addHandler(handler)
        logger.addFilter(existing_filter)
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
        before = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        privacy.initialize_rdkit_logging()
        assert (list(logger.handlers), logger.level, logger.propagate, logger.disabled) == before
        assert existing_filter in logger.filters
        from rdkit import rdBase
        rdBase.LogWarningMsg('synthetic-public-cold-import')
        with privacy.suppress_rdkit_diagnostics():
            rdBase.LogErrorMsg('synthetic-private-cold-import')
        assert len(messages) == 1 and 'synthetic-public-cold-import' in messages[0]
    ''')


@pytest.mark.parametrize('created', [False, True])
def test_cold_rdkit_import_retains_default_for_unconfigured_logger(created):
    result = fresh_python('''
        import logging
        import sys
        from src.system import rdkit_logging as privacy
        assert 'rdkit' not in sys.modules
        if CREATED:
            logging.getLogger('rdkit')  # Mere lookup is not configuration.
        privacy.initialize_rdkit_logging()
        logger = logging.getLogger('rdkit')
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], logging.StreamHandler)
        assert logger.level == logging.WARNING and logger.propagate is False
        from rdkit import rdBase
        rdBase.LogWarningMsg('synthetic-default-still-visible')
    '''.replace('CREATED', repr(created)))
    assert 'synthetic-default-still-visible' in result.stderr


@pytest.mark.parametrize("transition", ["held", "enter", "exit"])
def test_first_install_refuses_concurrent_blocklogs_without_mutation(transition):
    result = fresh_python('''
        import threading
        from rdkit import Chem, rdBase
        from src.system import rdkit_logging as privacy
        transition = TRANSITION
        ready, advance, done, release = [threading.Event() for _ in range(4)]
        failures = []
        def old_request():
            try:
                if transition == 'enter':
                    ready.set()
                    assert advance.wait(5)
                with rdBase.BlockLogs():
                    if transition != 'enter':
                        ready.set()
                        assert advance.wait(5)
                    if transition != 'exit':
                        done.set()
                        assert release.wait(5)
                        assert Chem.MolFromSmiles('C1NCCCCC') is None
                        assert 'rdApp.error:disabled' in rdBase.LogStatus()
                if transition == 'exit':
                    done.set()
                    assert release.wait(5)
            except BaseException as error:
                failures.append(error)
        worker = threading.Thread(target=old_request)
        worker.start()
        try:
            assert ready.wait(5)
            advance.set()
            assert done.wait(5)
            flags = rdBase.LogStatus()
            entered = False
            try:
                with privacy.suppress_rdkit_diagnostics():
                    entered = True
                    Chem.MolFromSmiles('C1CCCCCC')
            except RuntimeError as error:
                assert 'before starting worker threads' in str(error)
            assert rdBase.LogStatus() == flags, 'late installation changed native flags'
            assert not entered, 'late installation executed private work'
            assert not privacy._installed
        finally:
            release.set()
            worker.join(5)
        assert not worker.is_alive() and not failures, failures
        rdBase.LogWarningMsg('synthetic-public-native-after-refusal')
    '''.replace('TRANSITION', repr(transition)))
    assert 'C1NCCCCC' not in result.stderr + result.stdout
    assert 'C1CCCCCC' not in result.stderr + result.stdout
    assert 'synthetic-public-native-after-refusal' in result.stderr


def test_fresh_install_preserves_every_native_flag_combination():
    # Separate process per mask: repeated routing in one process is not first use.
    for mask in range(16):
        fresh_python('''
            import logging
            from rdkit import rdBase
            from src.system import rdkit_logging as privacy
            for bit, name in enumerate(('debug', 'info', 'warning', 'error')):
                (rdBase.EnableLog if MASK & (1 << bit) else rdBase.DisableLog)('rdApp.' + name)
            flags = rdBase.LogStatus()
            logger = logging.getLogger('rdkit')
            before = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
            assert not privacy._installed
            with privacy.suppress_rdkit_diagnostics():
                assert rdBase.LogStatus() == flags
            assert rdBase.LogStatus() == flags
            assert (list(logger.handlers), logger.level, logger.propagate, logger.disabled) == before
        '''.replace('MASK', str(mask)))


def test_first_install_inside_same_thread_native_block_restores_outer_flags():
    fresh_python('''
        from rdkit import rdBase
        from src.system import rdkit_logging as privacy
        flags = rdBase.LogStatus()
        with rdBase.BlockLogs():
            blocked = rdBase.LogStatus()
            with privacy.suppress_rdkit_diagnostics():
                with privacy.suppress_rdkit_diagnostics():
                    assert rdBase.LogStatus() == blocked
            assert rdBase.LogStatus() == blocked
        assert rdBase.LogStatus() == flags
    ''')


def test_nondefault_logger_configuration_keeps_other_threads_visible():
    fresh_python('''
        import logging
        import threading
        from rdkit import rdBase
        from src.system import rdkit_logging as privacy
        local, propagated = [], []
        class Capture(logging.Handler):
            def __init__(self, messages):
                super().__init__()
                self.messages = messages
            def emit(self, record):
                self.messages.append(record.getMessage())
        logger = logging.getLogger('rdkit')
        logger.addHandler(Capture(local))
        logging.getLogger().addHandler(Capture(propagated))
        logger.setLevel(logging.DEBUG)
        logger.propagate = True
        rdBase.EnableLog('rdApp.debug')
        rdBase.DisableLog('rdApp.info')
        flags = rdBase.LogStatus()
        before = (list(logger.handlers), logger.level, logger.propagate, logger.disabled)
        privacy.initialize_rdkit_logging()
        with privacy.suppress_rdkit_diagnostics():
            worker = threading.Thread(target=lambda: rdBase.LogDebugMsg('synthetic-public-debug'))
            worker.start()
            worker.join(5)
            assert not worker.is_alive()
            rdBase.LogErrorMsg('synthetic-private-error')
        rdBase.LogInfoMsg('synthetic-disabled-info')
        assert len(local) == 1 and 'synthetic-public-debug' in local[0]
        assert propagated == local
        assert (list(logger.handlers), logger.level, logger.propagate, logger.disabled) == before
        assert rdBase.LogStatus() == flags
    ''')


def test_router_partial_failure_restores_flags_and_does_not_enter(monkeypatch):
    route = rdBase.LogToPythonLogger
    rdBase.DisableLog('rdApp.debug')
    flags = rdBase.LogStatus()
    def broken():
        route()
        raise RuntimeError('synthetic-partial-router-failure')
    monkeypatch.setattr(rdkit_logging, '_installed', False)
    monkeypatch.setattr(rdBase, 'LogToPythonLogger', broken)
    with pytest.raises(RuntimeError, match='synthetic-partial-router-failure'):
        with rdkit_logging.suppress_rdkit_diagnostics():
            pytest.fail('private work executed after partial routing failure')
    assert rdBase.LogStatus() == flags
    assert not rdkit_logging._installed
    assert not getattr(rdkit_logging._local, 'depth', 0)


def test_runtime_constructor_bootstraps_before_dispatch():
    fresh_python('''
        from src.system import rdkit_logging as privacy
        from src.reverse_target.source_runtime import SourceRuntime
        assert not privacy._installed
        # Constructor does not read any source files or start a worker.
        runtime = SourceRuntime('synthetic-source-not-opened')
        try:
            assert privacy._installed, 'runtime left first install to its worker'
            assert runtime._thread is None
        finally:
            runtime.close()
    ''')


def test_asgi_bootstraps_before_application_imports():
    # Execute the real module prefix, stopping before dependencies/app construction
    # that could open databases or contact services. main.py also imports this app.
    fresh_python('''
        import ast
        from pathlib import Path
        from src.system import rdkit_logging as privacy
        tree = ast.parse(Path('src/web/app.py').read_text(encoding='utf-8'))
        prefix = []
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.level == 1:
                break
            prefix.append(node)
        assert not privacy._installed
        exec(compile(ast.Module(body=prefix, type_ignores=[]), 'app-bootstrap', 'exec'), {})
        assert privacy._installed, 'ASGI application did not bootstrap before dependencies'
    ''')


def test_late_compact_call_reports_bootstrap_contract_before_source_access():
    fresh_python('''
        import threading
        from rdkit import rdBase
        from src.reverse_target.compact_source import CompactSourcePolicy, load_compact_source
        flags = rdBase.LogStatus()
        errors, calls = [], []
        def load():
            try:
                load_compact_source({}, CompactSourcePolicy(), lambda: None,
                                    lambda text: calls.append(text))
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=load)
        worker.start()
        worker.join(5)
        assert not worker.is_alive()
        assert len(errors) == 1 and isinstance(errors[0], RuntimeError), errors
        assert 'before starting worker threads' in str(errors[0])
        assert not calls and rdBase.LogStatus() == flags
    ''')


@pytest.mark.parametrize('partial', [False, True])
def test_fresh_router_failure_cleans_filter_and_allows_safe_retry(partial):
    fresh_python('''
        import logging
        from rdkit import rdBase
        from src.system import rdkit_logging as privacy
        logger = logging.getLogger('rdkit')
        flags, filters = rdBase.LogStatus(), list(logger.filters)
        route = rdBase.LogToPythonLogger
        def broken():
            if PARTIAL:
                route()
            raise RuntimeError('synthetic-router-failure')
        rdBase.LogToPythonLogger = broken
        try:
            try:
                with privacy.suppress_rdkit_diagnostics():
                    raise AssertionError('unguarded work entered')
            except RuntimeError as error:
                assert str(error) == 'synthetic-router-failure'
            assert not privacy._installed
            assert logger.filters == filters
            assert rdBase.LogStatus() == flags
        finally:
            rdBase.LogToPythonLogger = route
        privacy.initialize_rdkit_logging()
        assert privacy._installed
        assert rdBase.LogStatus() == flags
        assert logger.filters.count(privacy._filter) == 1
    '''.replace('PARTIAL', repr(partial)))


def test_dispatched_but_not_registered_worker_prevents_bootstrap():
    fresh_python('''
        import threading
        from rdkit import rdBase
        from src.system import rdkit_logging as privacy
        entered, release = threading.Event(), threading.Event()
        worker = threading.Thread(target=lambda: None)
        bootstrap, wait = worker._bootstrap, worker._started.wait
        def held():
            entered.set()
            assert release.wait(5)
            bootstrap()
        def interrupted(*args, **kwargs):
            assert entered.wait(5)
            raise RuntimeError('interrupted-start-handshake')
        worker._bootstrap, worker._started.wait = held, interrupted
        try:
            try:
                worker.start()
            except RuntimeError:
                pass
            assert worker.ident is None and worker in threading.enumerate()
            flags = rdBase.LogStatus()
            try:
                privacy.initialize_rdkit_logging()
            except RuntimeError as error:
                assert 'before starting worker threads' in str(error)
            else:
                raise AssertionError('pending native dispatch was ignored')
            assert not privacy._installed and rdBase.LogStatus() == flags
        finally:
            worker._started.wait = wait
            release.set()
            assert wait(5)
            worker.join(5)
        assert not worker.is_alive()
    ''')


@pytest.mark.parametrize('transition', ['enter', 'exit'])
def test_bootstrapped_nested_guard_survives_native_block_interleavings(transition):
    result = fresh_python('''
        import threading
        from rdkit import Chem, rdBase
        from src.system import rdkit_logging as privacy
        privacy.initialize_rdkit_logging()
        ready, release = threading.Event(), threading.Event()
        errors = []
        def guarded():
            try:
                with privacy.suppress_rdkit_diagnostics():
                    with privacy.suppress_rdkit_diagnostics():
                        ready.set()
                        assert release.wait(5)
                        assert Chem.MolFromSmiles('C1NCCCCC') is None
                    assert Chem.MolFromSmiles('C1CCCCCC') is None
            except BaseException as error:
                errors.append(error)
        block = rdBase.BlockLogs() if TRANSITION == 'exit' else None
        worker = threading.Thread(target=guarded)
        try:
            worker.start()
            assert ready.wait(5)
            if TRANSITION == 'enter':
                block = rdBase.BlockLogs()
            else:
                del block
                block = None
            release.set()
            worker.join(5)
            assert not worker.is_alive() and not errors, errors
        finally:
            release.set()
            worker.join(5)
            del block
        rdBase.LogWarningMsg('synthetic-public-restored')
    '''.replace('TRANSITION', repr(transition)))
    assert 'C1NCCCCC' not in result.stderr + result.stdout
    assert 'C1CCCCCC' not in result.stderr + result.stdout
    assert 'synthetic-public-restored' in result.stderr


@pytest.mark.parametrize('mode', ['single', 'thread', 'tool'])
def test_cold_direct_entrypoints_keep_real_compact_results(tmp_path, mode):
    fresh_python('''
        import os
        import threading
        from pathlib import Path
        from src.system import rdkit_logging as privacy
        from tests.test_reverse_target_compact_source import write_source
        producer, _ = write_source(Path(DIRECTORY) / 'synthetic-source')
        assert not privacy._installed
        if MODE == 'tool':
            from src.agent.tools.reverse_target_tool import ReverseTargetTool
            os.environ['REVERSE_TARGET_DATA_DIR'] = str(producer.data_dir)
            tool = ReverseTargetTool()
            try:
                assert tool.start_preload()
                tool._source_runtime._thread.join(10)
                assert tool._source_runtime.snapshot()['state'] == 'ready'
                assert tool.execute('CCO')['success']
            finally:
                tool.close()
        else:
            errors = []
            def load():
                try:
                    producer.initialize_compact_strict()
                except BaseException as error:
                    errors.append(error)
            try:
                if MODE == 'thread':
                    privacy.initialize_rdkit_logging()
                    worker = threading.Thread(target=load)
                    worker.start()
                    worker.join(10)
                    assert not worker.is_alive()
                else:
                    load()
                assert not errors, errors
                result = producer.predict_with_receipt('CCO')
                assert result['receipt']['source']['row_count'] == 3
            finally:
                producer.close_strict()
    '''.replace('DIRECTORY', repr(str(tmp_path))).replace('MODE', repr(mode)))


@pytest.fixture
def warning_messages():
    logger = logging.getLogger("rdkit")
    messages = []

    class Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    handler = Capture()
    previous_level = logger.level
    was_enabled = "rdApp.warning:enabled" in rdBase.LogStatus()
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    rdBase.EnableLog("rdApp.warning")
    try:
        yield messages
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)
        if not was_enabled:
            rdBase.DisableLog("rdApp.warning")


def test_nested_suppression_restores_outer_guard_and_unguarded_visibility(warning_messages):
    with rdkit_logging.suppress_rdkit_diagnostics():
        rdBase.LogWarningMsg("synthetic-private-outer")
        with rdkit_logging.suppress_rdkit_diagnostics():
            rdBase.LogWarningMsg("synthetic-private-inner")
        rdBase.LogWarningMsg("synthetic-private-after-inner")
    rdBase.LogWarningMsg("synthetic-public-after-outer")
    assert len(warning_messages) == 1
    assert "synthetic-public-after-outer" in warning_messages[0]


def test_exception_restores_unguarded_visibility(warning_messages):
    with pytest.raises(ValueError, match="synthetic-failure"):
        with rdkit_logging.suppress_rdkit_diagnostics():
            rdBase.LogWarningMsg("synthetic-private-error")
            raise ValueError("synthetic-failure")
    rdBase.LogWarningMsg("synthetic-public-after-error")
    assert len(warning_messages) == 1
    assert "synthetic-public-after-error" in warning_messages[0]


def test_other_thread_remains_visible_during_guard(warning_messages):
    errors = []

    def emit():
        try:
            rdBase.LogWarningMsg("synthetic-public-other-thread")
            with rdkit_logging.suppress_rdkit_diagnostics():
                rdBase.LogWarningMsg("synthetic-private-other-thread")
            rdBase.LogWarningMsg("synthetic-public-other-restored")
        except BaseException as exc:
            errors.append(exc)

    with rdkit_logging.suppress_rdkit_diagnostics():
        worker = threading.Thread(target=emit)
        worker.start()
        worker.join(5)
        rdBase.LogWarningMsg("synthetic-private-main-thread")
    assert not worker.is_alive()
    assert not errors
    assert len(warning_messages) == 2
    assert "synthetic-public-other-thread" in warning_messages[0]
    assert "synthetic-public-other-restored" in warning_messages[1]


def test_installation_preserves_logger_configuration_and_native_flags(monkeypatch):
    logger = logging.getLogger("rdkit")
    handlers, level, propagate, disabled = list(logger.handlers), logger.level, logger.propagate, logger.disabled
    flags = rdBase.LogStatus()
    route = rdBase.LogToPythonLogger
    calls = []

    def install():
        calls.append(True)
        route()

    monkeypatch.setattr(rdkit_logging, "_installed", False)
    monkeypatch.setattr(rdBase, "LogToPythonLogger", install)
    with rdkit_logging.suppress_rdkit_diagnostics():
        pass
    with rdkit_logging.suppress_rdkit_diagnostics():
        pass
    assert calls == [True]
    assert logger.handlers == handlers
    assert (logger.level, logger.propagate, logger.disabled) == (level, propagate, disabled)
    assert rdBase.LogStatus() == flags
    assert logger.filters.count(rdkit_logging._filter) == 1


def test_routing_failure_does_not_execute_unprotected_work(monkeypatch):
    entered = []

    def unavailable():
        raise RuntimeError("synthetic-router-unavailable")

    monkeypatch.setattr(rdkit_logging, "_installed", False)
    monkeypatch.setattr(rdBase, "LogToPythonLogger", unavailable)
    with pytest.raises(RuntimeError, match="synthetic-router-unavailable"):
        with rdkit_logging.suppress_rdkit_diagnostics():
            entered.append(True)
    assert not entered
    assert not rdkit_logging._installed
    assert not getattr(rdkit_logging._local, "depth", 0)
