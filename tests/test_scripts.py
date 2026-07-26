from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_windows_task_scripts_use_the_local_runner_and_recovery_settings():
    install_script = (ROOT / "scripts" / "install_service.ps1").read_text(encoding="utf-8")
    uninstall_script = (ROOT / "scripts" / "uninstall_service.ps1").read_text(encoding="utf-8")

    assert "run.ps1" in install_script
    assert "New-ScheduledTaskTrigger -AtLogOn" in install_script
    assert "-RestartCount 3" in install_script
    assert "Register-ScheduledTask" in install_script
    assert "Unregister-ScheduledTask" in uninstall_script


def test_readme_documents_private_tailscale_serve_without_funnel():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "tailscale serve --bg 8000" in readme
    assert "Do not enable Tailscale Funnel" in readme
    assert "router port forwarding" in readme
