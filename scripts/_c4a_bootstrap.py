"""Private, fixed-selector offline pytest entry for the C4a Job owner."""
import json
import os
from pathlib import Path
import socket
import sys


def main():
    argv = sys.argv[1:]
    if len(argv) != 1:
        return 2
    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo))
    from scripts import run_c4a_acceptance as contract

    mode = argv[0]
    suite = mode in ("finite", "all")
    cases = contract.FINITE if mode == "finite" else (*contract.FINITE, *contract.STICKY)
    targets = ([f"{contract.NATIVE_FILE}::test_native_case[{case}]" for case in cases]
               if suite else [contract.child_selector(mode)])
    selected = dict(zip(targets, cases))
    contract.verify_suite_job()
    if suite:
        selected = {**{target: case for case, target in contract.OWNER_CONTROLS.items()}, **selected}
        targets = list(selected)
    contract.source_digest()
    native_connect = socket.socket.connect

    def blocked(*args, **kwargs):
        raise RuntimeError("c4a_offline_only")

    def connect(sock, address):
        caller = sys._getframe(1)
        if caller.f_code.co_name == "_fallback_socketpair" and caller.f_code.co_filename == socket.__file__:
            return native_connect(sock, address)
        return blocked()

    socket.socket.connect = connect
    socket.socket.connect_ex = socket.create_connection = blocked
    import site
    site.main()
    import pytest

    class Outcomes:
        def __init__(self):
            self.calls = []
            self.status = {}
            self.completed = []
            self.nodes = {}

        def pytest_collection_finish(self, session):
            for item in session.items:
                filename = Path(item.path).resolve().relative_to(repo).as_posix()
                target = filename + "::" + item.nodeid.split("::", 1)[1]
                self.nodes[item.nodeid] = target
            if list(self.nodes.values()) != targets:
                raise ValueError("c4a_fixed_collection_required")

        def pytest_runtest_setup(self, item):
            if suite and self.nodes[item.nodeid] != targets[len(self.completed)]:
                pytest.fail("previous required C4a case did not pass teardown")

        def pytest_runtest_makereport(self, item, call):
            if call.excinfo is not None:
                error = call.excinfo.value
                self.status[item.nodeid] = contract.failure_location(error)
                if not suite and mode == "clean" and type(error) is ValueError and error.args == ("c4a_guard_contract",):
                    (work / "invalid-guard-rejected").touch(exist_ok=False)

        def pytest_runtest_logreport(self, report):
            if report.when == "call" and report.passed:
                self.calls.append(report.nodeid)
            if suite and report.when == "teardown":
                case = selected[self.nodes[report.nodeid]]
                passed = report.nodeid in self.calls and report.passed and report.nodeid not in self.status
                record = {"case": case, "status": "passed" if passed else "failed"}
                if passed:
                    self.completed.append(case)
                if passed and case in contract.STICKY:
                    properties = dict(report.user_properties)
                    record.update({key: properties.get(key) for key in
                                   ("production_retention_verified", "test_child_containment_verified")})
                if report.nodeid in self.status:
                    record["failure"] = self.status[report.nodeid]
                # Only the child's bounded, path-free failure record is copied by the native case.
                if "c4a_child_failure" in dict(report.user_properties):
                    record["child_failure"] = dict(report.user_properties)["c4a_child_failure"]
                with (work / "cases.jsonl").open("a", encoding="ascii") as stream:
                    stream.write(json.dumps(record) + "\n")

    outcomes = Outcomes()
    work = Path(os.environ["TEMP"])
    os.chdir(work)
    code = pytest.main([*[str(repo / target) for target in targets], "-x", "-q",
                        "-p", "no:cacheprovider", "--tb=no", "--disable-warnings"], plugins=[outcomes])
    return 0 if code == 0 and len(outcomes.calls) == len(targets) else 1


if __name__ == "__main__":
    raise SystemExit(main())
