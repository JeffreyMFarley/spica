import logging
from collections import defaultdict, namedtuple
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set, Iterable

from botocore.exceptions import ClientError
from src.context import Context
from src.service import (
    AutoServiceGroup,
    EC2Instance,
    EksCluster,
    HostedZone,
    LambdaFunction,
    LoadBalancer,
    NatGateway,
    NetworkInterface,
    RdsInstance,
    SecurityGroup,
    ServiceInstance,
    Subnet,
    TargetGroup,
    VpcEndpoint,
)

# logger config
logger = logging.getLogger()
logging.basicConfig(level=logging.INFO, format="%(message)s")

# -----------------------------------------------------------------------------
# Helper Classes

Relation = namedtuple("Relation", ["source", "target"])


@dataclass
class AvailabilityZone:
    service_ids: List[str] = field(default_factory=list)
    subnet_ids: List[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Main Class


class VPC(object):
    def __init__(self, region, id: str) -> None:
        self.region = region
        self.id: str = id
        self.name: str = ""
        self.details: Dict[str, Any] = {}
        self._services: Dict[str, ServiceInstance] = {}
        self.relations: Set[Relation] = set()
        self.azs: Dict[str, AvailabilityZone] = defaultdict(AvailabilityZone)
        self.subnets: Dict[str, List[str]] = defaultdict(list)

    def __getitem__(self, key: str) -> ServiceInstance:
        return self._services.get(key, None)

    def __contains__(self, key: str) -> bool:
        return key in self._services

    @property
    def services(self) -> Iterable[ServiceInstance]:
        for v in self._services.values():
            yield v

    @property
    def availability_zones(self) -> Iterable[AvailabilityZone]:
        for v in self.azs.values():
            yield v

    def _add_relation(self, src: str, trg: str):
        if src is None or trg is None:
            return
        self.relations.add(Relation(src, trg))

    # ----------------------------------------------------------------------------
    # Describe Services

    def describe_asgs(self, context: Context):
        asgs = context.asg_client.describe_auto_scaling_groups()["AutoScalingGroups"]
        for asg in asgs:
            if self.asg_in_vpc(asg, context):
                _id = asg["AutoScalingGroupName"]
                self._services[_id] = AutoServiceGroup(asg, "ASG", _id)

                # Add relations
                for lb in asg["LoadBalancerNames"]:
                    self._add_relation(_id, lb)
                for x in asg["Instances"]:
                    self._add_relation(_id, x["InstanceId"])
                for az in asg["AvailabilityZones"]:
                    self.azs[az].service_ids.append(_id)

    def asg_in_vpc(self, asg, context):
        subnets_list = asg["VPCZoneIdentifier"].split(",")
        for subnet in subnets_list:
            try:
                sub_description = context.vpc_client.describe_subnets(
                    SubnetIds=[subnet]
                )["Subnets"]
                if sub_description[0]["VpcId"] == self.id:
                    return True
            except ClientError:
                pass

        return False

    def describe_ekss(self, context: Context):
        ekss = context.eks_client.list_clusters()["clusters"]

        for eks in ekss:
            eks_desc = context.eks_client.describe_cluster(name=eks)["cluster"]
            if eks_desc["resourcesVpcConfig"]["vpcId"] == self.id:
                _id = eks_desc["arn"]
                self._services[_id] = EksCluster(eks_desc, "EKS", _id)

                # Add relations
                for sn in eks_desc["resourcesVpcConfig"]["subnetIds"]:
                    self.subnets[sn].append(_id)
                for sg in eks_desc["resourcesVpcConfig"]["securityGroupIds"]:
                    self._add_relation(_id, sg)

    def describe_ec2s(self, context: Context):
        waiter = context.vpc_client.get_waiter("instance_terminated")
        reservations = context.vpc_client.describe_instances(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["Reservations"]

        # Get a list of ec2s
        ec2s = [ec2 for reservation in reservations for ec2 in reservation["Instances"]]

        for ec2 in ec2s:
            _id = ec2["InstanceId"]
            self._services[_id] = EC2Instance(ec2, "EC2", _id, ec2["InstanceType"])

            # Add relations
            for nwi in ec2["NetworkInterfaces"]:
                self._add_relation(_id, nwi["NetworkInterfaceId"])
            for sg in ec2["SecurityGroups"]:
                self._add_relation(_id, sg["GroupId"])
            self.subnets[ec2["SubnetId"]].append(_id)
            self.azs[ec2["Placement"]["AvailabilityZone"]].service_ids.append(_id)

    def describe_lambdas(self, context: Context):
        lmbds = context.lambda_client.list_functions()["Functions"]

        lambdas_list = [
            lmbd
            for lmbd in lmbds
            if "VpcConfig" in lmbd and lmbd["VpcConfig"]["VpcId"] == self.id
        ]

        for lmbda in lambdas_list:
            _id = lmbda["FunctionArn"]
            self._services[_id] = LambdaFunction(lmbda, "Lambda", _id)

            # Add relations
            for sn in lmbda["VpcConfig"]["SubnetIds"]:
                self.subnets[sn].append(_id)
            for sg in lmbda["VpcConfig"]["SecurityGroupIds"]:
                self._add_relation(_id, sg)

    def describe_rdss(self, context: Context):
        rdss = context.rds_client.describe_db_instances()["DBInstances"]

        rdsss_list = [rds for rds in rdss if rds["DBSubnetGroup"]["VpcId"] == self.id]

        for rds in rdsss_list:
            _id = rds["DBInstanceIdentifier"]
            self._services[_id] = RdsInstance(rds, "RDS", _id, rds["DBInstanceClass"])

            # Add relations
            for sn in rds["DBSubnetGroup"]["Subnets"]:
                self.subnets[sn["SubnetIdentifier"]].append(_id)
            for sg in rds["VpcSecurityGroups"]:
                self._add_relation(_id, sg["VpcSecurityGroupId"])
            self.azs[rds["AvailabilityZone"]].service_ids.append(_id)

    def describe_elbs(self, context: Context):
        elbs = context.elb_client.describe_load_balancers()["LoadBalancerDescriptions"]

        for elb in filter(lambda x: x["VPCId"] == self.id, elbs):
            _id = elb["LoadBalancerName"]
            self._services[_id] = LoadBalancer(elb, "ELBv1", _id)

            # Add relations
            for sn in elb["Subnets"]:
                self.subnets[sn].append(_id)
            for sg in elb["SecurityGroups"]:
                self._add_relation(_id, sg)
            for x in elb["Instances"]:
                self._add_relation(_id, x["InstanceId"])
            for az in elb["AvailabilityZones"]:
                self.azs[az].service_ids.append(_id)

    def describe_elbsV2(self, context: Context):
        elbs = context.elbV2_client.describe_load_balancers()["LoadBalancers"]

        for elb in filter(lambda x: x["VpcId"] == self.id, elbs):
            _id = elb["LoadBalancerArn"]
            self._services[_id] = LoadBalancer(elb, "ELBv2", elb["LoadBalancerArn"])

            # Add relations
            for az in elb["AvailabilityZones"]:
                self.subnets[az["SubnetId"]].append(_id)
                self.azs[az["ZoneName"]].service_ids.append(_id)
            for sg in elb.get("SecurityGroups", []):
                self._add_relation(_id, sg)

            hz_id = elb["CanonicalHostedZoneId"]
            try:
                hz = context.route53.get_hosted_zone(Id=hz_id)["HostedZone"]
            except:
                hz = {"Name": hz_id}
            self._services[hz_id] = HostedZone(hz, "HZ", hz_id)
            self._add_relation(hz_id, _id)

            # Add target groups
            tgs = context.elbV2_client.describe_target_groups(LoadBalancerArn=_id)[
                "TargetGroups"
            ]

            for tg in tgs:
                _tg_id = tg["TargetGroupArn"]

                if tg["VpcId"] != self.id:
                    logger.warn("Skipping %s. Not in same VPC", _tg_id)
                    continue

                self._services[_tg_id] = TargetGroup(tg, "TG", _tg_id)

                # This Target group belongs to this Load Balancer
                self._add_relation(_id, _tg_id)

                # Get the relations
                targets = context.elbV2_client.describe_target_health(
                    TargetGroupArn=_tg_id
                )["TargetHealthDescriptions"]

                for target in targets:
                    self._add_relation(_tg_id, target["Target"]["Id"])

    def describe_nats(self, context: Context):
        nats = context.vpc_client.describe_nat_gateways(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["NatGateways"]

        for nat in nats:
            _id = nat["NatGatewayId"]
            self._services[_id] = NatGateway(nat, "NAT", _id)

            # Add relations
            self.subnets[nat["SubnetId"]].append(_id)
            for nwi in nat["NatGatewayAddresses"]:
                self._add_relation(_id, nwi["NetworkInterfaceId"])

    def describe_enis(self, context: Context):
        enis = context.vpc_client.describe_network_interfaces(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["NetworkInterfaces"]

        for eni in enis:
            _id = eni["NetworkInterfaceId"]
            self._services[_id] = NetworkInterface(eni, "ENI", _id)

            # Add relations
            self.subnets[eni["SubnetId"]].append(_id)

    def describe_igws(self, context: Context):
        """
        Describe the internet gateway
        """
        igws = context.vpc_client.describe_internet_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [self.id]}]
        )["InternetGateways"]

        for igw in igws:
            _id = igw["InternetGatewayId"]
            self._services[_id] = ServiceInstance(igw, "IGW", _id)

    def describe_vpgws(self, context: Context):
        """
        Describe the virtual private gateway
        """

        # Get list of dicts
        vpgws = context.vpc_client.describe_vpn_gateways(
            Filters=[{"Name": "attachment.vpc-id", "Values": [self.id]}]
        )["VpnGateways"]

        for vpgw in vpgws:
            _id = vpgw["VpnGatewayId"]
            self._services[_id] = ServiceInstance(vpgw, "VPGW", _id)

            # Add relations
            if "AvailabilityZone" in vpgw:
                self.azs[vpgw["AvailabilityZone"]].services.append(_id)

    def describe_subnets(self, context: Context):
        # Get list of dicts of metadata
        subnets = context.vpc_client.describe_subnets(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["Subnets"]

        for subnet in subnets:
            _id = subnet["SubnetId"]
            self._services[_id] = Subnet(subnet, "SUBN", _id)

            # Add relations
            self.azs[subnet["AvailabilityZone"]].subnet_ids.append(_id)

    def describe_acls(self, context: Context):
        acls = context.vpc_client.describe_network_acls(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["NetworkAcls"]

        for acl in acls:
            _id = acl["NetworkAclId"]
            self._services[_id] = ServiceInstance(acl, "ACL", _id)

            # Add relations
            for sn in acl["Associations"]:
                self._add_relation(sn["SubnetId"], _id)

    def describe_sgs(self, context: Context):
        sgs = context.vpc_client.describe_security_groups(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["SecurityGroups"]

        for sg in sgs:
            _id = sg["GroupId"]
            _instance = SecurityGroup(sg, "SG", _id)
            self._services[_id] = _instance

            # Add relations
            for src, trg in _instance.relationships:
                self._add_relation(src, trg)

    def describe_rtbs(self, context: Context):
        rtbs = context.vpc_client.describe_route_tables(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["RouteTables"]

        for rtb in rtbs:
            _id = rtb["RouteTableId"]
            self._services[_id] = ServiceInstance(rtb, "RTB", _id)

            # Add relations
            for assoc in rtb["Associations"]:
                self._add_relation(assoc.get("SubnetId"), _id)
                self._add_relation(_id, assoc.get("GatewayId"))

            # Add relations
            for route in rtb["Routes"]:
                self._add_relation(_id, route.get("EgressOnlyInternetGatewayId"))
                self._add_relation(route.get("GatewayId"), _id)
                self._add_relation(_id, route.get("InstanceId"))
                self._add_relation(_id, route.get("NatGatewayId"))
                self._add_relation(_id, route.get("TransitGatewayId"))
                self._add_relation(_id, route.get("LocalGatewayId"))
                self._add_relation(_id, route.get("CarrierGatewayId"))
                self._add_relation(_id, route.get("NetworkInterfaceId"))
                self._add_relation(_id, route.get("VpcPeeringConnectionId"))

    def describe_hosted_zones(self, context: Context):
        hzs = context.route53.list_hosted_zones_by_vpc(
            VPCId=self.id, VPCRegion=context.region
        )["HostedZoneSummaries"]

        for hz in hzs:
            _id = hz["HostedZoneId"]
            self._services[_id] = HostedZone(hz, "HZ", _id)

    # HZ Record Sets
    # https://stackoverflow.com/questions/41716586/aws-route-53-listing-cname-records-using-boto3
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/route53/client/list_resource_record_sets.html
    # https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/elbv2/client/describe_load_balancers.html
    #    - CanonicalHostedZoneId

    def describe_vpc_epts(self, context: Context):
        epts = context.vpc_client.describe_vpc_endpoints(
            Filters=[{"Name": "vpc-id", "Values": [self.id]}]
        )["VpcEndpoints"]

        for ept in epts:
            _id = ept["VpcEndpointId"]
            self._services[_id] = VpcEndpoint.create(ept, _id)

            # Add relations
            for sn in ept["SubnetIds"]:
                self.subnets[sn].append(_id)
            for sg in ept["Groups"]:
                self._add_relation(_id, sg["GroupId"])
            for rtb in ept["RouteTableIds"]:
                self._add_relation(_id, rtb)
            for nwi in ept["NetworkInterfaceIds"]:
                self._add_relation(_id, nwi)

    def describe_vpc_peering_connections(self, context: Context):
        accepters = context.vpc_client.describe_vpc_peering_connections(
            Filters=[{"Name": "accepter-vpc-info.vpc-id", "Values": [self.id]}]
        )["VpcPeeringConnections"]

        for peering in accepters:
            _id = peering["VpcPeeringConnectionId"]
            self._services[_id] = ServiceInstance(peering, "PEER", _id)

        requesters = context.vpc_client.describe_vpc_peering_connections(
            Filters=[{"Name": "requester-vpc-info.vpc-id", "Values": [self.id]}]
        )["VpcPeeringConnections"]

        for peering in requesters:
            _id = peering["VpcPeeringConnectionId"]
            self._services[_id] = ServiceInstance(peering, "PEER", _id)

    # ----------------------------------------------------------------------------

    def cost_per_month(self):
        return sum([svc.cost_per_month for svc in self._services.values()])

    def scan(self, context: Context):
        self.details = context.vpc_client.describe_vpcs(VpcIds=[self.id])["Vpcs"][0]

        self.cidr_block = self.details["CidrBlock"]

        # Get the Name for the VPC
        tags = {t["Key"]: t["Value"] for t in self.details.get("Tags", [])}
        self.name = tags.get("Name", self.id)

        self.describe_asgs(context)
        self.describe_ec2s(context)
        self.describe_ekss(context)
        self.describe_elbs(context)
        self.describe_elbsV2(context)
        self.describe_enis(context)
        self.describe_hosted_zones(context)
        self.describe_igws(context)
        self.describe_lambdas(context)
        self.describe_nats(context)
        self.describe_rdss(context)
        self.describe_subnets(context)
        self.describe_vpc_epts(context)
        self.describe_vpgws(context)
        self.describe_vpc_peering_connections(context)

        if not context.options.ignore_free_resources:
            self.describe_acls(context)
            self.describe_rtbs(context)
            self.describe_sgs(context)

        # dhcpOpts https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/ec2.html#EC2.Client.describe_dhcp_optionscontext
        # Volume

    def to_csv(self, prefix, stream):
        fwd = f"{prefix}{self.name}\t"
        for svc_inst in self._services.values():
            svc_inst.to_csv(fwd, stream)
