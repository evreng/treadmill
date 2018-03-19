"""Treadmill constants.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import functools

import six

ALLOCATIONS = '/allocations'
APPGROUPS = '/app-groups'
APPGROUP_LOOKUP = '/appgroup-lookups'
APPMONITORS = '/app-monitors'
ARCHIVE_CONFIG = '/archive/config'
BLACKEDOUT_APPS = '/blackedout.apps'
BLACKEDOUT_SERVERS = '/blackedout.servers'
BUCKETS = '/buckets'
CELL = '/cell'
CRON_JOBS = '/cron-jobs'
ELECTION = '/election'
ENDPOINTS = '/endpoints'
EVENTS = '/events'
FINISHED = '/finished'
FINISHED_HISTORY = '/finished.history'
GLOBALS = '/globals'
IDENTITY_GROUPS = '/identity-groups'
KEYTAB_LOCKER = '/keytab-locker'
PARTITIONS = '/partitions'
PLACEMENT = '/placement'
REBOOTS = '/reboots'
RUNNING = '/running'
SCHEDULED = '/scheduled'
SCHEDULER = '/scheduler'
SERVERS = '/servers'
SERVER_PRESENCE = '/server.presence'
STATE_REPORTS = '/reports'
STRATEGIES = '/strategies'
TICKET_LOCKER = '/ticket-locker'
TICKETS = '/tickets'
TRACE = '/trace'
TRACE_HISTORY = '/trace.history'
TREADMILL = '/treadmill'
VERSION = '/version'
VERSION_HISTORY = '/version.history'
VERSION_ID = '/version-id'
ZOOKEEPER = '/zookeeper'

TRACE_SHARDS_COUNT = 256


def join_zookeeper_path(root, *child):
    """Returns zookeeper path joined by slash.
    """
    return '/'.join((root,) + child)


def make_path_f(zkpath):
    """Return closure that will construct node path.
    """
    return staticmethod(functools.partial(join_zookeeper_path, zkpath))


@staticmethod
def _path_trace_shard(shard_id):
    """Returns path of a trace shard.
    """
    shard = '{:04X}'.format(int(shard_id) % TRACE_SHARDS_COUNT)
    return '/'.join([TRACE, shard])


def trace_shards():
    """Return list of trace shards.
    """
    return ['/'.join([TRACE, '{:04X}'.format(idx)])
            for idx in six.moves.range(0, TRACE_SHARDS_COUNT)]


@staticmethod
def _path_trace(instancename, event=None):
    """Returns path of a trace object for given app instance.
    """
    instance_id = instancename[instancename.find('#') + 1:]
    shard = '{:04X}'.format(int(instance_id) % TRACE_SHARDS_COUNT)
    if event:
        nodename = '%s,%s' % (instancename, event)
        return '/'.join([TRACE, shard, nodename])
    else:
        return '/'.join([TRACE, shard])


@staticmethod
def _path_endpoint(name, proto, endpoint):
    """Returns path to Zk app endpoint node by name.

    The name is assumed to be <proid>.<xxx> which will result in the path:
    /endpoints/<proid>/<xxx>:<proto>:<endpoint>
    """
    prefix, _sep, rest = name.partition('.')
    return '/'.join(
        [ENDPOINTS, prefix, ':'.join([rest, proto, str(endpoint)])]
    )


@staticmethod
def _path_endpoint_proid(name):
    """Returns path to Zk app endpoint proid node path by name.

    The name is assumed to be <proid>.<xxx> which will result in the path:
    /endpoints/<proid>
    """
    proid, _sep, _rest = name.partition('.')
    return '/'.join([ENDPOINTS, proid])


class path(object):  # pylint: disable=C0103
    """Helper class to manage Zk namespace.
    """

    allocation = make_path_f(ALLOCATIONS)
    appgroup = make_path_f(APPGROUPS)
    appgroup_lookup = make_path_f(APPGROUP_LOOKUP)
    appmonitor = make_path_f(APPMONITORS)
    blackedout_app = make_path_f(BLACKEDOUT_APPS)
    blackedout_server = make_path_f(BLACKEDOUT_SERVERS)
    bucket = make_path_f(BUCKETS)
    cell = make_path_f(CELL)
    chroot = make_path_f(TREADMILL)
    event = make_path_f(EVENTS)
    identity_group = make_path_f(IDENTITY_GROUPS)
    partition = make_path_f(PARTITIONS)
    placement = make_path_f(PLACEMENT)
    reboot = make_path_f(REBOOTS)
    running = make_path_f(RUNNING)
    scheduled = make_path_f(SCHEDULED)
    scheduler = make_path_f(SCHEDULER)
    server_presence = make_path_f(SERVER_PRESENCE)
    server = make_path_f(SERVERS)
    strategy = make_path_f(STRATEGIES)
    tickets = make_path_f(TICKETS)
    ticket_locker = make_path_f(TICKET_LOCKER)
    keytab_locker = make_path_f(KEYTAB_LOCKER)
    version = make_path_f(VERSION)
    version_history = make_path_f(VERSION_HISTORY)
    version_id = make_path_f(VERSION_ID)
    zookeeper = make_path_f(ZOOKEEPER)
    election = make_path_f(ELECTION)
    finished = make_path_f(FINISHED)
    finished_history = make_path_f(FINISHED_HISTORY)
    trace_history = make_path_f(TRACE_HISTORY)
    trace_shard = make_path_f(TRACE)
    state_report = make_path_f(STATE_REPORTS)
    globals = make_path_f(GLOBALS)

    # Special methods
    endpoint = _path_endpoint
    endpoint_proid = _path_endpoint_proid
    trace = _path_trace
