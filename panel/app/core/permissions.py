import enum


class Permission(enum.StrEnum):
    """What a user may do on a server they were given access to. Admins may do everything."""

    VIEW = "view"  # see the server, its status and console output
    CONTROL = "control"  # start, stop, restart
    CONSOLE = "console"  # send console commands
    FILES = "files"  # file manager
    BACKUPS = "backups"  # create and download backups
    RESTORE = "restore"  # restore a backup: replaces the server's files
    SETTINGS = "settings"  # game settings and config files
    SCHEDULES = "schedules"  # manage schedules
    PLAYERS = "players"  # see who plays (with their addresses), kick and ban


# Every other permission needs VIEW: there is no controlling a server you can't see.
ALL = list(Permission)
