import asyncio
from textual.widgets import Input, Select, TabbedContent, TextArea
from smtp_test.app import SMTPApp
from smtp_test.core import Profile
from smtp_test.storage import save_profile, load_profiles, list_reports
from test_core import server


def test_tui_send_history_duplicate_and_export(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    with server() as port:
        save_profile(Profile(name="Local", host="127.0.0.1", port=port, security="Plain", sender="from@example.com"))
        async def check():
            app = SMTPApp()
            async with app.run_test(size=(120, 45)) as pilot:
                app.query_one("#profiles", Select).value = "Local"
                await pilot.pause()
                app.query_one("#copy-name", Input).value = "Copy"
                app.query_one("#duplicate").press()
                await pilot.pause()
                assert "Copy" in load_profiles()
                app.query_one("#recipient", Input).value = "to@example.com"
                app.query_one("#html", TextArea).text = "<b>hello</b>"
                app.start_test(True)
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert not app.busy
                assert app.last_report.metadata["delivery_status"] == "accepted"
                assert app.query_one("#message-id", Input).value.startswith("<")
                app.query_one("#output-tabs", TabbedContent).active = "tab-2"
                await pilot.pause()
                app.query_one("#reports", Select).value = str(list_reports()[0])
                await pilot.pause()
                target = tmp_path / "export.txt"
                app.query_one("#export-path", Input).value = str(target)
                app.query_one("#export-format", Select).value = "text"
                app.query_one("#export").press()
                await pilot.pause()
                assert "Transcript:" in target.read_text()
        asyncio.run(check())


def test_tui_stability_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("SMTP_TEST_HOME", str(tmp_path))
    with server() as port:
        async def check():
            app = SMTPApp()
            async with app.run_test(size=(100, 35)) as pilot:
                app.query_one("#host", Input).value = "127.0.0.1"
                app.query_one("#port", Input).value = str(port)
                app.query_one("#security", Select).value = "Plain"
                app.query_one("#repeats", Input).value = "2"
                app.query_one("#interval", Input).value = "0"
                app.start_diagnostic("stability")
                await app.workers.wait_for_complete()
                await pilot.pause()
                assert app.last_report.metadata["summary"]["PASS"] == 2
                assert not app.busy
        asyncio.run(check())
