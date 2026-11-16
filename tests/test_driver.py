from types import SimpleNamespace

from psychea.driver import INTERFACES, install_driver, wdi_command


def env(**kw):
    # A pretend Windows PC: admin, wdi found; run() records the commands.
    calls, said = [], []

    def run(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=kw.get("code", 0), stdout=b"", stderr=b"")
    base = dict(windows=True, admin=True, exe="wdi-64bit.exe", run=run, say=said.append)
    base.update({k: v for k, v in kw.items() if k != "code"})
    return base, calls, said


def test_command_matches_the_tested_wdi_usage():
    cmd = wdi_command("wdi.exe", 3, "tmp")
    assert cmd == ["wdi.exe", "--vid", "1356", "--pid", "642", "--iid", "3", "--type", "0", "--dest", "tmp"]


def test_installs_winusb_on_interfaces_0_1_3_in_order():
    e, calls, said = env()
    assert install_driver(**e) == 0
    assert [c[c.index("--iid") + 1] for c in calls] == [str(i) for i in INTERFACES] == ["0", "1", "3"]
    assert "WinUSB" in said[-1]


def test_not_windows_needs_nothing():
    e, calls, said = env(windows=False)
    assert install_driver(**e) == 0 and calls == [] and "not needed" in said[-1]


def test_needs_administrator_and_runs_nothing_without():
    e, calls, said = env(admin=False)
    assert install_driver(**e) == 1 and calls == [] and "administrator" in said[-1].lower()


def test_works_with_the_psp_unplugged():
    e, calls, said = env()
    assert install_driver(**e) == 0 and len(calls) == 3


def test_missing_installer_points_to_zadig():
    e, calls, said = env(exe=None)
    assert install_driver(**e) == 1 and calls == [] and "Zadig" in said[-1]


def test_a_failed_interface_is_reported_and_fails_the_command():
    e, calls, said = env(code=1)
    assert install_driver(**e) == 1 and len(calls) == 3 and "failed" in said[-1].lower()


def test_the_shipped_installer_is_found_next_to_the_code():
    from psychea.driver import find_wdi
    assert find_wdi().endswith(("wdi-64bit.exe", "wdi-32bit.exe"))


import os  # noqa: E402

import pytest  # noqa: E402

from psychea import driver  # noqa: E402


def test_an_unplugged_run_names_the_exact_command_per_interface_with_a_fresh_folder_each(tmp_path, monkeypatch):
    made = iter(str(tmp_path / n) for n in "abc")
    monkeypatch.setattr(driver.tempfile, "mkdtemp", lambda: next(made))
    e, calls, said = env()
    assert install_driver(**e) == 0
    assert [c[-1] for c in calls] == [str(tmp_path / n) for n in "abc"]
    assert all(c[0] == "wdi-64bit.exe" for c in calls)
    assert said[:2] == ["Installing WinUSB on interface 0 ...", "Installing WinUSB on interface 1 ..."]
    assert said[2] == "Installing WinUSB on interface 3 ..."


def test_the_success_message_tells_when_windows_picks_the_driver_up():
    e, calls, said = env()
    install_driver(**e)
    assert len(said) == 4 and said[-1].startswith("WinUSB is registered for the PSP camera interfaces.")
    assert said[-1].endswith("so nothing needs to be plugged in now).")


def test_each_message_has_one_job_when_nothing_is_run():
    e, _, said = env(windows=False)
    install_driver(**e)
    assert said == ["A driver is not needed on this system (Linux/macOS use libusb as it is)."]
    e, _, said = env(admin=False)
    install_driver(**e)
    assert said == ["Run this command in a terminal opened as Administrator (right click -> Run as administrator)."]
    e, _, said = env(exe=None)
    install_driver(**e)
    assert len(said) == 1 and said[0].startswith("The driver installer is missing (uv sync, or install WinUSB")
    assert said[0].endswith("WinUSB -> Replace driver).")


def test_not_windows_wins_over_missing_admin_and_missing_installer():
    e, calls, said = env(windows=False, admin=False, exe=None)
    assert install_driver(**e) == 0 and calls == [] and "not needed" in said[0]


def test_a_failed_interface_shows_exit_code_and_the_tools_error_text_and_the_others_still_run():
    codes = iter([0, 7, 0])
    said, calls = [], []

    def run(cmd):
        calls.append(cmd)
        return SimpleNamespace(returncode=next(codes), stdout=b"out text", stderr=b"  boom \xff\n")
    assert install_driver(True, True, "wdi.exe", run, said.append) == 1
    assert len(calls) == 3
    assert said[2] == "  failed (exit code 7): boom \ufffd"
    assert said[-1] == "Driver install failed on interface(s) [1]. Try Zadig by hand (see the README)."


def test_the_tools_stdout_is_shown_when_it_has_no_stderr_and_all_failures_are_listed():
    said = []

    def run(cmd):
        return SimpleNamespace(returncode=2, stdout=b"only out\n", stderr=b"")
    assert install_driver(True, True, "wdi.exe", run, said.append) == 1
    assert "  failed (exit code 2): only out" in said
    assert said[-1].startswith("Driver install failed on interface(s) [0, 1, 3].")


@pytest.mark.parametrize("arch,machine,expected", [
    ("64bit", "AMD64", "wdi-64bit.exe"), ("64bit", "x86", "wdi-64bit.exe"), ("32bit", "x86", "wdi-32bit.exe"),
    ("32bit", "AMD64", "wdi-64bit.exe"), ("32bit", "amd64", "wdi-64bit.exe"), ("32bit", "ARM64", "wdi-32bit.exe")])
def test_the_installer_matches_the_pythons_bitness_even_in_a_32bit_python_on_a_64bit_windows(
        monkeypatch, arch, machine, expected):
    monkeypatch.setattr(driver.platform, "architecture", lambda: (arch, "WindowsPE"))
    monkeypatch.setattr(driver.platform, "machine", lambda: machine)
    monkeypatch.setattr(driver.os.path, "exists", lambda path: True)
    path = driver.find_wdi()
    assert os.path.basename(path) == expected
    assert os.path.basename(os.path.dirname(path)) == "binaries"
    assert os.path.dirname(os.path.dirname(path)) == os.path.dirname(os.path.abspath(driver.__file__))


def test_no_installer_file_means_none(monkeypatch):
    monkeypatch.setattr(driver.os.path, "exists", lambda path: False)
    assert driver.find_wdi() is None


@pytest.mark.parametrize("value,expected", [(1, True), (0, False)])
def test_is_admin_asks_windows(monkeypatch, value, expected):
    import ctypes
    shell32 = SimpleNamespace(IsUserAnAdmin=lambda: value)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=shell32), raising=False)
    assert driver.is_admin() is expected


def test_is_admin_is_false_where_there_is_no_windows_api_or_it_fails(monkeypatch):
    import ctypes
    monkeypatch.delattr(ctypes, "windll", raising=False)
    assert driver.is_admin() is False

    def fail():
        raise OSError("no")
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(shell32=SimpleNamespace(IsUserAnAdmin=fail)), raising=False)
    assert driver.is_admin() is False


def test_run_install_wires_the_real_world_into_the_installer(monkeypatch):
    ran, printed = [], []
    monkeypatch.setattr(driver.sys, "platform", "win32")
    monkeypatch.setattr(driver, "is_admin", lambda: True)
    monkeypatch.setattr(driver, "find_wdi", lambda: "wdi.exe")
    monkeypatch.setattr(driver.subprocess, "run", lambda cmd, **kw: ran.append((cmd, kw)) or SimpleNamespace(
        returncode=0, stdout=b"", stderr=b""))
    monkeypatch.setattr("builtins.print", printed.append)
    assert driver.run_install() == 0
    assert [cmd[cmd.index("--iid") + 1] for cmd, _ in ran] == ["0", "1", "3"]
    assert all(kw == {"capture_output": True} for _, kw in ran)
    assert printed[-1].startswith("WinUSB is registered")


def test_run_install_is_a_no_op_off_windows(monkeypatch):
    printed = []
    monkeypatch.setattr(driver.sys, "platform", "linux")
    monkeypatch.setattr("builtins.print", printed.append)
    assert driver.run_install() == 0 and "not needed" in printed[0]


def test_an_installer_that_cannot_be_started_fails_that_interface_and_the_others_still_run():
    said, calls = [], []

    def run(cmd):
        calls.append(cmd)
        if len(calls) == 2:
            raise PermissionError(13, "Access is denied")          # e.g. a virus scanner blocks wdi-simple
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")
    assert install_driver(True, True, "wdi.exe", run, said.append) == 1
    assert len(calls) == 3
    assert any(line.startswith("  could not run the installer:") and "Access is denied" in line for line in said)
    assert said[-1] == "Driver install failed on interface(s) [1]. Try Zadig by hand (see the README)."


def test_an_installer_that_runs_but_prints_nothing_is_reported_by_its_exit_code():
    said = []
    run = lambda cmd: SimpleNamespace(returncode=-3, stdout=b"", stderr=None)       # noqa: E731
    assert install_driver(True, True, "wdi.exe", run, said.append) == 1
    assert "  failed (exit code -3): " in said
