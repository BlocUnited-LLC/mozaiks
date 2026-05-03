"""
CLI command modules.
"""

from mozaiks_cli.commands import init as init_command
from mozaiks_cli.commands import onboard as onboard_command
from mozaiks_cli.commands import quickstart as quickstart_command
from mozaiks_cli.commands import serve as serve_command
from mozaiks_cli.commands import studio as studio_command
from mozaiks_cli.commands import add as add_command
from mozaiks_cli.commands import info as info_command
from mozaiks_cli.commands import gen as gen_command

__all__ = [
    "quickstart_command",
    "init_command",
    "onboard_command",
    "serve_command",
    "studio_command",
    "add_command",
    "info_command",
    "gen_command",
]
