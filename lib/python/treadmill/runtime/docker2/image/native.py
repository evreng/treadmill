"""A collection of native images.
"""

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function
from __future__ import unicode_literals

import errno
import glob
import io
import logging
import os
import pwd
import shutil
import stat

from treadmill import appcfg
from treadmill import cgroups
from treadmill import fs
from treadmill import keytabs
from treadmill import runtime
from treadmill import subproc
from treadmill import supervisor
from treadmill import utils

from treadmill.fs import linux as fs_linux

from . import fs as image_fs
from . import _image_base
from . import _repository_base


_LOGGER = logging.getLogger(__name__)

_CONTAINER_ENV_DIR = 'env'


def create_environ_dir(container_dir, root_dir, app):
    """Creates environ dir for s6-envdir."""
    env_dir = os.path.join(container_dir, _CONTAINER_ENV_DIR)

    env = {
        'TREADMILL_APP': app.app,
        'TREADMILL_CELL': app.cell,
        'TREADMILL_CPU': app.cpu,
        'TREADMILL_DISK': app.disk,
        'TREADMILL_HOST_IP': app.network.external_ip,
        'TREADMILL_IDENTITY': app.identity,
        'TREADMILL_IDENTITY_GROUP': app.identity_group,
        'TREADMILL_INSTANCEID': app.task,
        'TREADMILL_MEMORY': app.memory,
        'TREADMILL_PROID': app.proid,
        'TREADMILL_ENV': app.environment,
    }

    for endpoint in app.endpoints:
        envname = 'TREADMILL_ENDPOINT_{0}'.format(endpoint.name.upper())
        env[envname] = endpoint.real_port

    env['TREADMILL_EPHEMERAL_TCP_PORTS'] = ' '.join(
        [str(port) for port in app.ephemeral_ports.tcp]
    )
    env['TREADMILL_EPHEMERAL_UDP_PORTS'] = ' '.join(
        [str(port) for port in app.ephemeral_ports.udp]
    )

    env['TREADMILL_CONTAINER_IP'] = app.network.vip
    env['TREADMILL_GATEWAY_IP'] = app.network.gateway
    if app.shared_ip:
        env['TREADMILL_SERVICE_IP'] = app.network.external_ip

    supervisor.create_environ_dir(env_dir, env)

    # Bind the environ directory in the container volume
    fs.mkdir_safe(os.path.join(root_dir, _CONTAINER_ENV_DIR))
    fs_linux.mount_bind(
        root_dir, os.path.join(os.sep, _CONTAINER_ENV_DIR),
        source=os.path.join(container_dir, _CONTAINER_ENV_DIR),
        recursive=False, read_only=True
    )


def create_supervision_tree(tm_env, container_dir, root_dir, app,
                            cgroups_path):
    """Creates s6 supervision tree."""
    uniq_name = appcfg.app_unique_name(app)
    ctl_uds = os.path.join(os.sep, 'run', 'tm_ctl')
    tombstone_ctl_uds = os.path.join(ctl_uds, 'tombstone')

    sys_dir = os.path.join(container_dir, 'sys')
    sys_scandir = supervisor.create_scan_dir(
        sys_dir,
        finish_timeout=6000,
        wait_cgroups=cgroups_path,
    )
    for svc_def in app.system_services:
        if svc_def.restart is not None:
            monitor_policy = {
                'limit': svc_def.restart.limit,
                'interval': svc_def.restart.interval,
                'tombstone': {
                    'uds': False,
                    'path': tm_env.services_tombstone_dir,
                    'id': '{},{}'.format(uniq_name, svc_def.name)
                }
            }
        else:
            monitor_policy = None

        supervisor.create_service(
            sys_scandir,
            name=svc_def.name,
            app_run_script=svc_def.command,
            userid='root',
            environ_dir=os.path.join(container_dir, _CONTAINER_ENV_DIR),
            environ={
                envvar.name: envvar.value
                for envvar in svc_def.environ
            },
            environment=app.environment,
            downed=svc_def.downed,
            trace=None,
            monitor_policy=monitor_policy
        )
    sys_scandir.write()

    services_dir = os.path.join(container_dir, 'services')
    services_scandir = supervisor.create_scan_dir(
        services_dir,
        finish_timeout=5000
    )

    for svc_def in app.services:

        if svc_def.restart is not None:
            monitor_policy = {
                'limit': svc_def.restart.limit,
                'interval': svc_def.restart.interval,
                'tombstone': {
                    'uds': True,
                    'path': tombstone_ctl_uds,
                    'id': '{},{}'.format(uniq_name, svc_def.name)
                }
            }
        else:
            monitor_policy = None

        if svc_def.trace is not None:
            trace = {
                'instanceid': app.name,
                'uniqueid': app.uniqueid,
                'service': svc_def.name,
                'path': os.path.join(ctl_uds, 'appevents')
            }
        else:
            trace = None

        logger_template = getattr(svc_def, 'logger', 's6.app-logger.run')
        _LOGGER.info('Using logger: %s', logger_template)

        supervisor.create_service(
            services_scandir,
            name=svc_def.name,
            app_run_script=svc_def.command,
            userid=svc_def.proid,
            environ_dir='/' + _CONTAINER_ENV_DIR,
            environ={
                envvar.name: envvar.value
                for envvar in svc_def.environ
            },
            environment=app.environment,
            downed=False,
            trace=trace if svc_def.trace else None,
            log_run_script=logger_template,
            monitor_policy=monitor_policy
        )
    services_scandir.write()

    # Bind the service directory in the container volume
    fs.mkdir_safe(os.path.join(root_dir, 'services'))
    fs_linux.mount_bind(
        root_dir, os.path.join(os.sep, 'services'),
        source=os.path.join(container_dir, 'services'),
        recursive=False, read_only=False
    )

    # Bind the ctrl directory in the container volume which has all the
    # unix domain sockets to communicate outside the container to treadmill
    fs.mkdir_safe(os.path.join(root_dir, 'run', 'tm_ctl'))
    fs_linux.mount_bind(
        root_dir, os.path.join(os.sep, 'run', 'tm_ctl'),
        source=tm_env.ctl_dir,
        recursive=False, read_only=False
    )


def make_fsroot(root_dir):
    """Initializes directory structure for the container in a new root.

    The container uses pretty much a blank a FHS 3 layout.

     - Bind directories in parent / (with exceptions - see below.)
     - Skip /tmp, create /tmp in the new root with correct permissions.
     - Selectively create / bind /var.
       - /var/tmp (new)
       - /var/log (new)
       - /var/spool - create empty with dirs.
     - Bind everything in /var, skipping /spool/tickets
     """
    newroot_norm = fs.norm_safe(root_dir)

    emptydirs = [
        '/bin',
        '/dev',
        '/etc',
        '/home',
        '/lib',
        '/lib64',
        '/opt',
        '/proc',
        '/root',
        '/run',
        '/sbin',
        '/sys',
        '/tmp',
        '/usr',
        '/var/cache',
        '/var/empty',
        '/var/lib',
        '/var/lock',
        '/var/log',
        '/var/opt',
        '/var/spool',
        '/var/tmp',
        '/var/spool/keytabs',
        '/var/spool/tickets',
        '/var/spool/tokens',
    ]

    stickydirs = [
        '/opt',
        '/run',
        '/tmp',
        '/var/cache',
        '/var/lib',
        '/var/lock',
        '/var/log',
        '/var/opt',
        '/var/tmp',
        '/var/spool/keytabs',
        '/var/spool/tickets',
        '/var/spool/tokens',
    ]

    # these folders are shared with underlying host and other containers,
    mounts = [
        '/bin',
        '/etc',  # TODO: Add /etc/opt
        '/lib',
        '/lib64',
        '/root',
        '/sbin',
        '/usr',
        # TODO: Remove below once PAM UDS is implemented
        '/var/tmp/treadmill/env',
        '/var/tmp/treadmill/spool',
    ]

    for directory in emptydirs:
        fs.mkdir_safe(newroot_norm + directory)

    for directory in stickydirs:
        os.chmod(newroot_norm + directory, 0o777 | stat.S_ISVTX)

    # /var/empty must be owned by root and not group or world-writable.
    os.chmod(os.path.join(newroot_norm, 'var/empty'), 0o711)

    fs_linux.mount_bind(
        newroot_norm, os.path.join(os.sep, 'sys'),
        source='/sys',
        recursive=True, read_only=False
    )
    # TODO: For security, /dev/ should be minimal and separated to each
    #       container.
    fs_linux.mount_bind(
        newroot_norm, os.path.join(os.sep, 'dev'),
        source='/dev',
        recursive=True, read_only=False
    )
    # Per FHS3 /var/run should be a symlink to /run which should be tmpfs
    fs.symlink_safe(
        os.path.join(newroot_norm, 'var', 'run'),
        '/run'
    )
    # We create an unbounded tmpfs mount so that runtime data can be written to
    # it, counting against the memory limit of the container.
    fs_linux.mount_tmpfs(newroot_norm, '/run')

    # Make shared directories/files readonly to container
    for mount in mounts:
        if os.path.exists(mount):
            fs_linux.mount_bind(
                newroot_norm, mount,
                recursive=True, read_only=True
            )

    # /etc/docker as temp fs as dockerd create /etc/docker/key.json
    fs_linux.mount_tmpfs(newroot_norm, '/etc/docker')


def create_overlay(tm_env, container_dir, root_dir, app):
    """Create overlay configuration files for the container.
    """
    # ldpreloads
    _prepare_ldpreload(container_dir, app)
    # hosts
    _prepare_hosts(container_dir, app)
    # resolv.conf
    _prepare_resolv_conf(tm_env, container_dir)
    # sshd PAM configuration
    _prepare_pam_sshd(tm_env, container_dir, app)
    # constructed keytab.
    _prepare_krb(tm_env, container_dir, root_dir, app)
    # bind prepared inside container
    _bind_overlay(container_dir, root_dir)


def _prepare_krb(tm_env, container_dir, root_dir, app):
    """Manage kerberos environment inside container.
    """
    etc_dir = os.path.join(container_dir, 'overlay', 'etc')
    fs.mkdir_safe(etc_dir)
    kt_dest = os.path.join(etc_dir, 'krb5.keytab')
    kt_sources = glob.glob(os.path.join(tm_env.spool_dir, 'keytabs', 'host#*'))
    keytabs.make_keytab(kt_dest, kt_sources)

    for kt_spec in app.keytabs:
        if ':' in kt_spec:
            owner, princ = kt_spec.split(':', 1)
        else:
            owner = kt_spec
            princ = kt_spec

        kt_dest = os.path.join(root_dir, 'var', 'spool', 'keytabs', owner)
        kt_sources = glob.glob(os.path.join(tm_env.spool_dir, 'keytabs',
                                            '%s#*' % princ))
        keytabs.make_keytab(kt_dest, kt_sources, owner)


def _prepare_ldpreload(container_dir, app):
    """Add mandatory ldpreloads to the container environment.
    """
    etc_dir = os.path.join(container_dir, 'overlay', 'etc')
    fs.mkdir_safe(etc_dir)
    new_ldpreload = os.path.join(etc_dir, 'ld.so.preload')

    try:
        shutil.copyfile('/etc/ld.so.preload', new_ldpreload)
    except IOError as err:
        if err.errno != errno.ENOENT:
            raise
        _LOGGER.info('/etc/ld.so.preload not found, creating empty.')
        utils.touch(new_ldpreload)

    ldpreloads = []
    if app.ephemeral_ports.tcp or app.ephemeral_ports.udp:
        treadmill_bind_preload = subproc.resolve('treadmill_bind_preload.so')
        ldpreloads.append(treadmill_bind_preload)

    if not ldpreloads:
        return

    _LOGGER.info('Configuring /etc/ld.so.preload: %r', ldpreloads)
    with io.open(new_ldpreload, 'a') as f:
        f.write('\n'.join(ldpreloads) + '\n')


def _prepare_hosts(container_dir, app):
    """Create a hosts file for the container.

    overlay/
        /etc/
            hosts           # hosts file to be bind mounted in container.
        /run/
            /host-aliases/  # Directory to be bind mounted in container.
    """
    etc_dir = os.path.join(container_dir, 'overlay', 'etc')
    ha_dir = os.path.join(container_dir, 'overlay', 'run', 'host-aliases')
    fs.mkdir_safe(etc_dir)
    fs.mkdir_safe(ha_dir)

    shutil.copyfile(
        '/etc/hosts',
        os.path.join(etc_dir, 'hosts')
    )

    pwnam = pwd.getpwnam(app.proid)
    os.chown(ha_dir, pwnam.pw_uid, pwnam.pw_gid)


def _prepare_pam_sshd(tm_env, container_dir, app):
    """Override pam.d sshd stack with special sshd pam stack.
    """
    pamd_dir = os.path.join(container_dir, 'overlay', 'etc', 'pam.d')
    fs.mkdir_safe(pamd_dir)
    new_pam_sshd = os.path.join(pamd_dir, 'sshd')

    if app.shared_network:
        template_pam_sshd = os.path.join(
            tm_env.root, 'etc', 'pam.d', 'sshd.shared_network'
        )
    else:
        template_pam_sshd = os.path.join(
            tm_env.root, 'etc', 'pam.d', 'sshd'
        )

    if not os.path.exists(template_pam_sshd):
        _LOGGER.warning('Falling back to local PAM sshd config.')
        template_pam_sshd = '/etc/pam.d/sshd'

    shutil.copyfile(
        template_pam_sshd,
        new_pam_sshd
    )


def _prepare_resolv_conf(tm_env, container_dir):
    """Create an resolv.conf file for the container.
    """
    etc_dir = os.path.join(container_dir, 'overlay', 'etc')
    fs.mkdir_safe(etc_dir)
    new_resolv_conf = os.path.join(etc_dir, 'resolv.conf')

    # TODO(boysson): This should probably be based instead on /etc/resolv.conf
    #                for other resolver options
    template_resolv_conf = os.path.join(tm_env.root, 'etc', 'resolv.conf')
    if not os.path.exists(template_resolv_conf):
        _LOGGER.warning('Falling back to local resolver config.')
        template_resolv_conf = '/etc/resolv.conf'

    shutil.copyfile(
        template_resolv_conf,
        new_resolv_conf
    )


def _bind_overlay(container_dir, root_dir):
    """Create the overlay in the container.

    :param ``str`` container_dir:
        Base directory of container data/config.
    :param ``str`` root_dir:
        New root directory of the container.
    """
    # Overlay overrides container configs
    #   - /etc/resolv.conf, so that container always uses dnscache.
    #   - pam.d sshd stack with special sshd pam that unshares network.
    #   - /etc/ld.so.preload to enforce necessary system hooks
    #
    overlay_dir = os.path.join(container_dir, 'overlay')
    for overlay_file in ['etc/hosts',
                         'etc/krb5.keytab',
                         'etc/ld.so.preload',
                         'etc/pam.d/sshd',
                         'etc/resolv.conf']:
        fs_linux.mount_bind(
            root_dir, os.path.join(os.sep, overlay_file),
            source=os.path.join(overlay_dir, overlay_file),
            recursive=False, read_only=True)

    # Mount host-aliases as read-write
    fs_linux.mount_bind(
        root_dir, os.path.join(os.sep, 'run', 'host-aliases'),
        source=os.path.join(overlay_dir, 'run', 'host-aliases'),
        recursive=False, read_only=False
    )

    # Also override resolv.conf in the current mount namespace so that
    # system services have access to our resolver.
    fs_linux.mount_bind(
        '/', '/etc/resolv.conf',
        source=os.path.join(overlay_dir, 'etc/resolv.conf'),
        recursive=False, read_only=True
    )


def get_cgroup_path(app):
    """Gets the path of the cgroup."""
    unique_name = appcfg.app_unique_name(app)
    cgrp = os.path.join('treadmill', 'apps', unique_name)
    return cgrp


class NativeImage(_image_base.Image):
    """Represents a native image."""

    __slots__ = (
        'tm_env',
    )

    def __init__(self, tm_env):
        self.tm_env = tm_env

    def unpack(self, container_dir, root_dir, app):
        make_fsroot(root_dir)

        image_fs.configure_plugins(self.tm_env, container_dir, app)

        # FIXME: Lots of things are still reading this file.
        #        Copy updated state manifest as app.json in the
        #        container_dir so it is visible in chrooted env.
        shutil.copy(os.path.join(container_dir, runtime.STATE_JSON),
                    os.path.join(root_dir, appcfg.APP_JSON))

        cgrp = get_cgroup_path(app)

        create_environ_dir(container_dir, root_dir, app)
        create_supervision_tree(
            self.tm_env, container_dir, root_dir, app,
            cgroups_path=cgroups.makepath(
                'memory', cgrp
            )
        )
        create_overlay(self.tm_env, container_dir, root_dir, app)


class NativeImageRepository(_repository_base.ImageRepository):
    """A collection of native images."""

    def get(self, url):
        return NativeImage(self.tm_env)
