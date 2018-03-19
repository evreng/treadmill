"""Kills all connections from a given treadmill server."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import logging
import re

import click
import kazoo

from treadmill import presence
from treadmill import utils
from treadmill import zkutils
from treadmill import context
from treadmill import cli
from treadmill import zknamespace as z


_LOGGER = logging.getLogger(__name__)

_ON_EXCEPTIONS = cli.handle_exceptions([
    (kazoo.exceptions.NoAuthError, 'Error: not authorized.'),
    (context.ContextError, None),
])


def _gen_formatter(fields, formatter):
    """Generate real formatter to have item index in position."""
    pattern = re.compile(r'(%(\w))')
    match = pattern.findall(formatter)

    mapping = {field: index for index, field in enumerate(fields)}

    # (symbol, key) should be ('%t', 't')
    for (symbol, key) in match:
        index = mapping[key]
        formatter = formatter.replace(symbol, '{%d}' % index, 1)

    return formatter


def _list_server_blackouts(zkclient, fmt):
    """List server blackouts."""

    with_partition = '%p' in fmt
    with_version = '%v' in fmt

    blackouts = []
    for node in zkclient.get_children(z.BLACKEDOUT_SERVERS):
        try:
            node_path = z.path.blackedout_server(node)
            data, metadata = zkutils.get_with_metadata(zkclient, node_path)
        except kazoo.client.NoNodeError:
            continue

        partition, version = None, None

        if with_partition:
            server_data = zkutils.get_default(
                zkclient, z.path.server(node)
            )
            if server_data and server_data.get('partition'):
                partition = server_data['partition']

        if with_version:
            version_data = zkutils.get_default(
                zkclient, z.path.version(node)
            )
            if version_data and version_data.get('codepath'):
                version = version_data['codepath']

        blackouts.append((metadata.created, node, partition, version, data))

    # [%t] %h %r will be printed as below
    # [Thu, 05 May 2016 02:59:58 +0000] <hostname> -
    fields = ('t', 'h', 'p', 'v', 'r')
    formatter = _gen_formatter(fields, fmt)

    for when, node, partition, version, reason in reversed(sorted(blackouts)):
        cli.out(formatter.format(
            utils.strftime_utc(when),
            node,
            partition or '-',
            version or '-',
            reason or '-'
        ))


def _clear_server_blackout(zkclient, server):
    """Clear server blackout."""
    path = z.path.blackedout_server(server)
    zkutils.ensure_deleted(zkclient, path)


def _blackout_server(zkclient, server, reason):
    """Blackout server."""
    if not reason:
        raise click.UsageError('--reason is required.')

    path = z.path.blackedout_server(server)
    zkutils.ensure_exists(
        zkclient,
        path,
        acl=[zkutils.make_host_acl(server, 'rwcda')],
        data=str(reason)
    )
    presence.kill_node(zkclient, server)


def _blackout_app(zkclient, app, clear):
    """Blackout app."""
    # list current blacklist
    blacklisted_node = z.path.blackedout_app(app)
    if clear:
        zkutils.ensure_deleted(zkclient, blacklisted_node)
    else:
        zkutils.ensure_exists(zkclient, blacklisted_node)


def _list_blackedout_apps(zkclient):
    """List blackedout apps."""
    try:
        for blacklisted in zkclient.get_children(z.BLACKEDOUT_APPS):
            print(blacklisted)
    except kazoo.client.NoNodeError:
        pass


def init():
    """Top level command handler."""

    @click.group()
    @click.option('--cell', required=True,
                  envvar='TREADMILL_CELL',
                  callback=cli.handle_context_opt,
                  expose_value=False)
    def blackout():
        """Manage server and app blackouts."""
        pass

    @blackout.command(name='server')
    @click.option('--server', help='Server name to blackout.')
    @click.option('--reason', help='Blackout reason.')
    @click.option('--fmt', help='Format of the blackout output.',
                  default='[%t] %h %r')
    @click.option('--clear', is_flag=True, default=False,
                  help='Clear blackout.')
    @_ON_EXCEPTIONS
    def server_cmd(server, reason, fmt, clear):
        """Manage server blackout."""
        if server is not None:
            if clear:
                _clear_server_blackout(context.GLOBAL.zk.conn, server)
            else:
                _blackout_server(context.GLOBAL.zk.conn, server, reason)
        else:
            _list_server_blackouts(context.GLOBAL.zk.conn, fmt)

    @blackout.command(name='app')
    @click.option('--app', help='App name to blackout.')
    @click.option('--clear', is_flag=True, default=False,
                  help='Clear blackout.')
    def app_cmd(app, clear):
        """Manage app blackouts."""
        if app:
            _blackout_app(context.GLOBAL.zk.conn, app, clear)

        _list_blackedout_apps(context.GLOBAL.zk.conn)

    del server_cmd
    del app_cmd
    return blackout
