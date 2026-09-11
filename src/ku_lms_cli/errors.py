"""Safe public errors shared by the live provider and media transport."""


class LiveCommandError(RuntimeError):
    """A safe-to-print live command failure."""


class LoginExpired(LiveCommandError):
    """The player requires authentication again."""
