"""Syncronizes cell Zookeeper with LDAP data.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import collections
import hashlib
import json
import io
import logging
import sqlite3
import tempfile

from treadmill import admin
from treadmill import context
from treadmill import fs
from treadmill import utils
from treadmill import zknamespace as z
from treadmill import zkutils
from treadmill.scheduler import masterapi

_LOGGER = logging.getLogger(__name__)


def _match_appgroup(group):
    """Match if appgroup belongs to the cell.
    """
    return context.GLOBAL.cell in group.get('cells', [])


def _sync_collection(zkclient, entities, zkpath, match=None):
    """Sync ldap collection to Zookeeper.
    """
    _LOGGER.info('Sync: %s', zkpath)
    zkclient.ensure_path(zkpath)

    in_zk = zkclient.get_children(zkpath)

    to_sync = {}
    for entity in entities:
        name = entity.pop('_id')
        if match and not match(entity):
            _LOGGER.debug('Skip: %s', name)
            continue
        to_sync[name] = entity

    for to_del in set(in_zk) - set(to_sync):
        _LOGGER.info('Delete: %s', to_del)
        zkutils.ensure_deleted(zkclient, z.join_zookeeper_path(zkpath, to_del))

    # Add or update current app-groups
    for name, entity in to_sync.items():
        if zkutils.put(zkclient, z.join_zookeeper_path(zkpath, name),
                       entity, check_content=True):
            _LOGGER.info('Update: %s', name)
        else:
            _LOGGER.info('Up to date: %s', name)


def _appgroup_group_by_proid(cell_app_groups):
    """Group list of app groups by proid pattern."""
    # create reverse lookup of appgroups by proid.
    def _key(item):
        return (item.get('pattern'),
                item.get('group-type'),
                item.get('endpoints'),
                item.get('data'))

    groups_by_proid = collections.defaultdict(list)
    checksum_by_proid = collections.defaultdict(hashlib.sha1)

    for group in sorted(cell_app_groups, key=_key):
        data = json.dumps(utils.equals_list2dict(group.get('data', [])))
        pattern = group.get('pattern')
        if not pattern:
            _LOGGER.warning('Invalid app-group, no pattern: %r', group)
            continue

        proid, _rest = pattern.split('.', 1)
        # Create a flat table, and endpoints is a list.
        endpoints = ','.join(group.get('endpoints', []))
        group_type = group.get('group-type')
        row = (pattern, group_type, endpoints, data)
        groups_by_proid[proid].append(row)
        for item in row:
            if item:
                checksum_by_proid[proid].update(item.encode('utf8'))

    return groups_by_proid, checksum_by_proid


def _create_lookup_db(rows):
    """Create lookup db file."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        pass

    conn = sqlite3.connect(f.name)
    with conn:
        conn.execute(
            """
            CREATE TABLE appgroups (
                pattern text,
                group_type text,
                endpoints text,
                data text
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO appgroups (
                pattern, group_type, endpoints, data
            ) VALUES(?, ?, ?, ?)
            """,
            rows
        )
    conn.close()
    return f.name


def _sync_appgroup_lookups(zkclient, cell_app_groups):
    """Sync app group lookup databases."""
    groups_by_proid, checksum_by_proid = _appgroup_group_by_proid(
        cell_app_groups
    )

    for proid in groups_by_proid:

        if not groups_by_proid[proid]:
            _LOGGER.debug('Appgroups not defined for proid: %s', proid)
            zkutils.ensure_deleted(z.path.appgroup_lookup, proid)
            continue

        # If node already exists with the proper checksum, ensure that others
        # are removed, but not recreate.
        digest = checksum_by_proid[proid].hexdigest()
        if zkclient.exists(z.path.appgroup_lookup(proid, digest)):
            _LOGGER.debug('Appgroup lookup for proid %s is up to date: %s',
                          proid, digest)
            continue

        db_file = _create_lookup_db(groups_by_proid[proid])
        try:
            _save_appgroup_lookup(zkclient, db_file, proid, digest)
        finally:
            fs.rm_safe(db_file)


def _save_appgroup_lookup(zkclient, db_file, proid, digest):
    """Save appgroup lookup to Zookeeper."""
    with io.open(db_file, 'rb') as f:
        zkutils.put(zkclient, z.path.appgroup_lookup(proid, digest),
                    f.read())

    _remove_extra_appgroup_lookup(zkclient, proid, digest)


def _remove_extra_appgroup_lookup(zkclient, proid, digest):
    """Remove extra app group lookups, leaving the only current one."""
    lookup_path = z.path.appgroup_lookup(proid)
    for node in zkclient.get_children(lookup_path):
        if node == digest:
            continue

        zkutils.ensure_deleted(zkclient, z.path.appgroup_lookup(proid, node))


def sync_appgroups():
    """Sync app-groups from LDAP to Zookeeper."""
    _LOGGER.info('Sync appgroups.')
    admin_app_group = admin.AppGroup(context.GLOBAL.ldap.conn)
    app_groups = admin_app_group.list({})
    cell_app_groups = [group for group in app_groups if _match_appgroup(group)]
    _sync_collection(context.GLOBAL.zk.conn,
                     cell_app_groups, z.path.appgroup())
    _sync_appgroup_lookups(context.GLOBAL.zk.conn, cell_app_groups)


def sync_partitions():
    """Syncs partitions to Zookeeper.
    """
    _LOGGER.info('Sync: partitions.')
    zkclient = context.GLOBAL.zk.conn

    admin_cell = admin.Cell(context.GLOBAL.ldap.conn)
    partitions = admin_cell.partitions(context.GLOBAL.cell)

    zkclient.ensure_path(z.path.partition())

    in_zk = zkclient.get_children(z.path.partition())
    names = [partition['_id'] for partition in partitions]

    for extra in set(in_zk) - set(names):
        _LOGGER.debug('Delete: %s', extra)
        zkutils.ensure_deleted(zkclient, z.path.partition(extra))

    # Add or update current partitions
    for partition in partitions:
        zkname = partition['_id']

        if 'reboot-schedule' in partition:
            try:
                partition['reboot-schedule'] = utils.reboot_schedule(
                    partition['reboot-schedule']
                )
            except ValueError:
                _LOGGER.info('Invalid reboot schedule, ignoring.')

        if zkutils.put(zkclient, z.path.partition(zkname),
                       partition, check_content=True):
            _LOGGER.info('Update: %s', zkname)
        else:
            _LOGGER.info('Up to date: %s', zkname)


def sync_allocations():
    """Syncronize allocations.
    """
    _LOGGER.info('Sync allocations.')
    zkclient = context.GLOBAL.zk.conn

    admin_alloc = admin.CellAllocation(context.GLOBAL.ldap.conn)
    allocations = admin_alloc.list({'cell': context.GLOBAL.cell})

    filtered = []
    for alloc in allocations:
        _LOGGER.info('Sync allocation: %s', alloc)
        name, _cell = alloc['_id'].rsplit('/', 1)
        alloc['name'] = name
        filtered.append(alloc)

    masterapi.update_allocations(zkclient, filtered)


def sync_servers():
    """Sync global servers list."""
    _LOGGER.info('Sync servers.')
    admin_srv = admin.Server(context.GLOBAL.ldap.conn)
    global_servers = admin_srv.list({})
    zkutils.ensure_exists(
        context.GLOBAL.zk.conn,
        z.path.globals('servers'),
        data=[server['_id'] for server in global_servers]
    )
