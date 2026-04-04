# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownParameterType=false
"""Tests for the ladon CLI (ladon.cli)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from ladon.cli import build_parser, load_plugin_class, main

# ---------------------------------------------------------------------------
# load_plugin_class
# ---------------------------------------------------------------------------


class TestLoadPluginClass:
    def test_loads_known_class(self) -> None:
        cls = load_plugin_class("ladon.runner:RunConfig")
        from ladon.runner import RunConfig

        assert cls is RunConfig

    def test_missing_colon_exits(self) -> None:
        with pytest.raises(SystemExit):
            load_plugin_class("ladon.runner.RunConfig")

    def test_unknown_module_exits(self) -> None:
        with pytest.raises(SystemExit):
            load_plugin_class("ladon.does_not_exist:Foo")

    def test_unknown_attribute_exits(self) -> None:
        with pytest.raises(SystemExit):
            load_plugin_class("ladon.runner:NoSuchClass")

    def test_multiple_colons_exits(self) -> None:
        """'a:b:c' is ambiguous — must be rejected, not silently misparse."""
        with pytest.raises(SystemExit):
            load_plugin_class("ladon.runner:Run:Config")

    def test_empty_module_path_exits(self) -> None:
        """:ClassName raises ValueError in importlib — must be caught and exit."""
        with pytest.raises(SystemExit):
            load_plugin_class(":ClassName")


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


class TestParser:
    def test_version_flag_exits_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """argparse action='version' prints and exits 0."""
        parser = build_parser()
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["--version"])
        assert exc_info.value.code == 0
        assert "ladon" in capsys.readouterr().out

    def test_info_command(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["info"])
        assert args.command == "info"

    def test_run_requires_plugin_and_ref(self) -> None:
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["run"])

    def test_run_with_args(self) -> None:
        parser = build_parser()
        args = parser.parse_args(
            ["run", "--plugin", "pkg:Cls", "--ref", "http://example.com"]
        )
        assert args.command == "run"
        assert args.plugin == "pkg:Cls"
        assert args.ref == "http://example.com"

    def test_respect_robots_txt_defaults_false(self) -> None:
        """--respect-robots-txt must default to False for backward compat."""
        parser = build_parser()
        args = parser.parse_args(
            ["run", "--plugin", "pkg:Cls", "--ref", "http://example.com"]
        )
        assert args.respect_robots_txt is False

    def test_respect_robots_txt_flag_sets_true(self) -> None:
        """Passing --respect-robots-txt must set the flag to True."""
        parser = build_parser()
        args = parser.parse_args(
            [
                "run",
                "--plugin",
                "pkg:Cls",
                "--ref",
                "http://example.com",
                "--respect-robots-txt",
            ]
        )
        assert args.respect_robots_txt is True


# ---------------------------------------------------------------------------
# main() dispatch
# ---------------------------------------------------------------------------


class TestMainDispatch:
    def test_version_prints_version(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("sys.argv", ["ladon", "--version"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 0
        assert "ladon" in capsys.readouterr().out

    def test_info_prints_version_and_python(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch("sys.argv", ["ladon", "info"]):
            main()
        out = capsys.readouterr().out
        assert "ladon" in out
        assert "python" in out

    def test_no_command_exits_nonzero(self) -> None:
        with patch("sys.argv", ["ladon"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code != 0

    def test_run_calls_run_crawl(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from ladon.runner import RunResult

        mock_result = RunResult(
            record=None,
            leaves_consumed=3,
            leaves_persisted=3,
            leaves_failed=0,
            errors=(),
        )

        fake_plugin_cls = MagicMock(return_value=MagicMock())

        with (
            patch(
                "sys.argv",
                [
                    "ladon",
                    "run",
                    "--plugin",
                    "pkg:Cls",
                    "--ref",
                    "http://x.com",
                ],
            ),
            patch("ladon.cli.load_plugin_class", return_value=fake_plugin_cls),
            patch("ladon.runner.run_crawl", return_value=mock_result),
        ):
            # All leaves succeeded — must not raise SystemExit (exit code 0)
            main()

        out = capsys.readouterr().out
        assert "leaves_consumed" in out
        assert "3" in out

    @pytest.mark.parametrize(
        "bad_ref",
        [
            "not-a-url",  # no scheme, no netloc
            "http://",  # scheme present, netloc empty
            "ftp://host/path",  # unsupported scheme
        ],
    )
    def test_run_exits_on_invalid_ref(self, bad_ref: str) -> None:
        """A non-URL --ref or unsupported scheme must be rejected with exit code 1."""
        with (
            patch(
                "sys.argv",
                [
                    "ladon",
                    "run",
                    "--plugin",
                    "pkg:Cls",
                    "--ref",
                    bad_ref,
                ],
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_run_exits_on_plugin_instantiation_failure(self) -> None:
        """Plugin __init__ raising must exit without leaking the HttpClient."""
        exploding_cls = MagicMock(side_effect=RuntimeError("bad config"))

        with (
            patch(
                "sys.argv",
                [
                    "ladon",
                    "run",
                    "--plugin",
                    "pkg:Cls",
                    "--ref",
                    "http://x.com",
                ],
            ),
            patch("ladon.cli.load_plugin_class", return_value=exploding_cls),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_run_exits_1_on_run_crawl_exception(self) -> None:
        """A generic exception from run_crawl must exit with code 1."""
        fake_plugin_cls = MagicMock(return_value=MagicMock())

        with (
            patch(
                "sys.argv",
                [
                    "ladon",
                    "run",
                    "--plugin",
                    "pkg:Cls",
                    "--ref",
                    "http://x.com",
                ],
            ),
            patch("ladon.cli.load_plugin_class", return_value=fake_plugin_cls),
            patch(
                "ladon.runner.run_crawl",
                side_effect=RuntimeError("network failure"),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 1

    def test_run_exits_3_on_expansion_not_ready(self) -> None:
        """ExpansionNotReadyError must exit with code 3 (retry later)."""
        from ladon.plugins.errors import ExpansionNotReadyError

        fake_plugin_cls = MagicMock(return_value=MagicMock())

        with (
            patch(
                "sys.argv",
                [
                    "ladon",
                    "run",
                    "--plugin",
                    "pkg:Cls",
                    "--ref",
                    "http://x.com",
                ],
            ),
            patch("ladon.cli.load_plugin_class", return_value=fake_plugin_cls),
            patch(
                "ladon.runner.run_crawl",
                side_effect=ExpansionNotReadyError("not yet"),
            ),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 3

    def test_run_exits_2_on_failures(self) -> None:
        from ladon.runner import RunResult

        mock_result = RunResult(
            record=None,
            leaves_consumed=0,
            leaves_persisted=0,
            leaves_failed=1,
            errors=("ref[0]: something broke",),
        )

        fake_plugin_cls = MagicMock(return_value=MagicMock())

        with (
            patch(
                "sys.argv",
                [
                    "ladon",
                    "run",
                    "--plugin",
                    "pkg:Cls",
                    "--ref",
                    "http://x.com",
                ],
            ),
            patch("ladon.cli.load_plugin_class", return_value=fake_plugin_cls),
            patch("ladon.runner.run_crawl", return_value=mock_result),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
        assert exc_info.value.code == 2
