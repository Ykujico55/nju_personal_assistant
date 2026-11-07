"""Stable, content-free diagnostics exposed across the extension boundary."""


class FilesError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
