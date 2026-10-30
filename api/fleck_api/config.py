import os


def setting(name: str, default: str) -> str:
    return os.environ.get(name, default)
