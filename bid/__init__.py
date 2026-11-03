try:
    from importlib.metadata import PackageNotFoundError, version

    __version__ = version("breakitdown")
except PackageNotFoundError:
    import pathlib
    import tomllib

    project = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    __version__ = "0.2.0rc1"
