from treadmill.infra.utils.elb.manager import ELBManager
from treadmill.dirwatch import DirWatcher
import os
import re
import logging

ZKFS_DIR = '/tmp/zkfs-test'
_LOGGER = logging.getLogger(__name__)


class EndpointWatcher(ELBManager):
    def __init__(self, zkfs_dir):
        ELBManager.__init__(self, zkfs_dir)
        self.on_created = self._on_created
        self.on_deleted = self._on_deleted

    def set_context(self, path):
        self.proid, self.fileName = path.split('/')[-2:]
        self.app, self.cell, self.protocol = re.search('([a-z0-9-]+)\.([a-z0-9-]+)#\d*:\w+:(\w+)', self.fileName).groups()
        self.lb_name = "{}-{}-{}-{}".format(self.proid, self.app, self.cell, self.protocol)
        self.fileRegex = '{}.{}#\d+:.*:{}'.format(self.app, self.cell, self.protocol)
        self.proid_dir = '{}/{}'.format(self.endpoint_dir, self.proid)

    def getTargetFromFile(self, path):
        instanceId, port = open(path).read().split(':')
        return instanceId, int(port)

    def getEndPoints(self, path):
        files = ["{}/{}".format(dir, fileName) for fileName in os.listdir(self.proid_dir)
                 if re.match(self.fileRegex, fileName)
                 ]
        return [self.getTargetFromFile(file) for file in files]

    def _on_created(self, path):
        self.set_context(path)
        print("Found new endpoint {}".format(path))
        targets = [self.getTargetFromFile(path)]
        self.register(self.lb_name, targets)
        print("{} created".format(self.lb_name))
        tg = self.findTargetGroup(name=self.lb_name)
        self.add_targets(tg, targets)
        print("Processed adding of endpoint {}".format(targets))

    def _on_deleted(self, path):
        self.set_context(path)
        print("Deleted endpoint {}".format(path))
        targets = self.getEndPoints(path)
        tg = self.findTargetGroup(name=self.lb_name)
        if targets:
            self.remove_targets(tg, targets)
            print("Processed removing of endpoint {}".format(path))
        else:
            self.deregister(self.lb_name)
            print("Load balancer {} has been removed (no targets)".format(self.lb_name))

    def run(self):
        watch = DirWatcher()
        watch.on_created = self._on_created
        watch.on_deleted = self._on_deleted
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
apw.run()



