from socket import gethostbyname as nslookup
from treadmill.infra.utils.elb.services import ELBClient
from time import sleep


class ELBManager(ELBClient):
    def __init__(self, root_dir):
        ELBClient.__init__(self)
        self.root_dir = root_dir
        self.endpoint_dir = '{}/endpoints'.format(self.root_dir)

    def register(self, elb_name, endpoints):
        instances = list(self.ec2client.instances.filter(InstanceIds=[i for i, p in endpoints]))
        vpcId = set([instance.vpc_id for instance in instances]).pop()
        elb = self.get_or_create_load_balancer(elb_name, vpcId)
        tg = self.get_or_create_target_group(elb_name, vpcId, endpoints, 'TCP', 8000)
        listener = self.get_or_create_elb_listener(elb, 8000, [tg])

        try:
            sg = [sg for sg in list(self.ec2client.security_groups.all())
                  if sg.vpc_id == tg.vpcId
                  and sg.group_name == elb_name].pop()
        except:
            sg = self.ec2client.create_security_group(
                Description="treadmill loadbalancer healthCheck",
                GroupName=elb_name,
                VpcId=tg.vpcId)
        while True:
            try:
                elb = self.get_or_create_load_balancer(elb_name, vpcId)
                dnsName = nslookup(elb.dnsName)
                break
            except:
                sleep(3)
        for perm in sg.ip_permissions:
            sg.revoke_ingress(CidrIp=perm["IpRanges"].pop()["CidrIp"],
                              FromPort=perm["FromPort"],
                              ToPort=perm["ToPort"],
                              IpProtocol=perm["IpProtocol"].upper())
        sg.authorize_ingress(
            IpPermissions=[{
                "FromPort": 22,
                "ToPort": 22,
                "IpProtocol": 'tcp',
                    "IpRanges": [{
                                "CidrIp": "{}/32".format(dnsName),
                                "Description": "ELB: {}".format(elb_name),
                            }]
                    }]
            )
        for instance in instances:
            sgs = list(set([sg.get('GroupId') for sg in instance.security_groups] + [sg.id]))
            instance.modify_attribute(Groups=sgs)

    def deregister(self, elb_name):
        tg = self.findTargetGroup(name=elb_name)
        elb = self.findLoadBalancer(name=elb_name)
        if elb:
            self.client.delete_load_balancer(LoadBalancerArn=elb.arn)
        if tg:
            self.client.delete_target_group(TargetGroupArn=tg.arn)
