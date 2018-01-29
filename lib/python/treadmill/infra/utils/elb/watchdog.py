from treadmill.infra.utils.elb.manager import ELBManager
from treadmill.dirwatch import DirWatcher
from types import SimpleNamespace
import os
import re
import logging
from threading import Thread

ZKFS_DIR = '/tmp/zkfs-test'
_LOGGER = logging.getLogger(__name__)


class EndpointWatcher(ELBManager):
    def __init__(self, zkfs_dir):
        ELBManager.__init__(self, zkfs_dir)
        self.on_created = self._on_created
        self.on_deleted = self._on_deleted

    def get_context(self, path):
        c = SimpleNamespace()
        c.proid, c.fileName = path.split('/')[-2:]
        c.app, c.cell, c.protocol = re.search('([a-z0-9-]+)\.([a-z0-9-]+)#\d*:\w+:(\w+)', c.fileName).groups()
        c.lb_name = "{}-{}-{}-{}".format(c.proid, c.app, c.cell, c.protocol)
        c.fileRegex = '{}.{}#\d+:.*:{}'.format(c.app, c.cell, c.protocol)
        c.proid_dir = '{}/{}'.format(self.endpoint_dir, c.proid)
        return c

    def getTargetFromFile(self, path):
        instanceId, port = open(path).read().split(':')
        return instanceId, int(port)

    def getEndPoints(self, path):
        context = self.get_context(path)
        files = ["{}/{}".format(context.proid_dir, fileName) for fileName in os.listdir(context.proid_dir)
                 if re.match(context.fileRegex, fileName)
                 ]
        return [self.getTargetFromFile(file) for file in files if os.path.exists(file)]

    def _on_created(self, path):
        context = self.get_context(path)
        print("Found new endpoint {}/{}".format(context.proid_dir, context.fileName))
        targets = [self.getTargetFromFile(path)]
        self.register(context.lb_name, targets)
        print("{} created".format(context.lb_name))
        tg = self.findTargetGroup(name=context.lb_name)
        if tg:
            self.add_targets(tg, targets)
        print("Processed adding of endpoint {}".format(targets))

    def _on_deleted(self, path):
        context = self.get_context(path)
        print("Deleted endpoint {}/{}".format(context.proid_dir, context.fileName))
        targets = self.getEndPoints(path)
        tg = self.findTargetGroup(name=context.lb_name)
        if tg and targets:
            self.remove_targets(tg, targets)
            print("Processed removing of endpoint {}".format(path))
        else:
            self.deregister(context.lb_name)
            print("Load balancer {} has been removed (no targets)".format(context.lb_name))

    def _on_modified(self, path):
        self._on_created(path)
        self._on_deleted(path)

    def run(self):
        watch = DirWatcher()
        watch.on_created = self._on_created
        watch.on_deleted = self._on_deleted
        watch.on_modified = self._on_modified
        while True:
            apps = list(set([d.split('.')[0] for d in os.listdir(self.endpoint_dir)]))
            for app in apps:
                if not app:
                    continue
                appdir = "/".join((self.endpoint_dir, app))
                watch.add_dir(appdir)
            if watch.wait_for_events(5):
                watch.process_events()


apw = EndpointWatcher(ZKFS_DIR)
t = Thread(target=apw.run)
t.daemon = True
t.start()

