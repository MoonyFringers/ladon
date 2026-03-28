import ladon


def test_version_exposed() -> None:
    assert isinstance(ladon.__version__, str)
    assert ladon.__version__  # non-empty
