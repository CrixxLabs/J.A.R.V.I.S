from unittest.mock import patch, MagicMock


def test_pc_doctor_routes_only_explicit_diagnostics():
    from skills import pc_doctor
    assert pc_doctor.can_handle("PC doctor, check my PC")
    assert pc_doctor.can_handle("why is my pc slow")
    assert not pc_doctor.can_handle("what is a CPU?")


def test_project_dev_routes_narrowly():
    from skills import project_dev_assistant as skill
    assert skill.can_handle("git status")
    assert skill.can_handle("run the tests")
    assert not skill.can_handle("explain Python decorators")


def test_project_status_is_read_only():
    from skills import project_dev_assistant as skill
    fake = MagicMock(returncode=0, stdout=" M planner.py\n", stderr="")
    with patch("subprocess.run", return_value=fake) as run:
        result = skill.handle("project status")
    assert "1 changed path" in result
    args = run.call_args.args[0]
    assert args == ["git", "status", "--short"]


def test_project_test_command_uses_tests_directory():
    from skills import project_dev_assistant as skill
    fake = MagicMock(returncode=0, stdout="190 passed in 1.00s\n", stderr="")
    with patch("subprocess.run", return_value=fake) as run:
        result = skill.handle("run the tests")
    assert "Tests passed" in result
    args = run.call_args.args[0]
    assert args[1:] == ["-m", "pytest", "tests", "-q"]


def test_example_skill_template_is_not_present_in_production_tree():
    from pathlib import Path
    assert not (Path(__file__).parents[1] / "skills" / "example_skill.py").exists()
