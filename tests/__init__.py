import builtins


def open_utf8(file, mode="r", *args, **kwargs):
    if "b" not in mode:
        kwargs.setdefault("encoding", "utf-8")
    return builtins.open(file, mode, *args, **kwargs)
