import logging
from dataclasses import dataclass
from typing import Tuple

from src.utils import parse_arn


@dataclass
class ServiceInstance:
    details: dict
    service_name: str
    instance_name: str

    def to_csv(self, prefix, stream):
        cells = "\t".join(
            [
                self.service_name,
                self.id,
                self.name,
                self.type_info,
                f"{self.cost_per_month:,.2f}",
            ]
        )
        stream.write(f"{prefix}{cells}\n")

    @property
    def cost_per_month(self):
        return 0

    @property
    def id(self):
        # It's an ARN, shorten
        if self.instance_name.startswith("arn"):
            elems = parse_arn(self.instance_name)
            return elems["resource"]

        return self.instance_name

    @property
    def label(self):
        result = self.instance_name

        for tag in self.details.get("Tags", []):
            if tag["Key"] == "Name":
                result += f"\\n{tag['Value']}"

        return result

    @property
    def name(self):
        # It's an ARN, shorten
        if self.instance_name.startswith("arn"):
            elems = parse_arn(self.instance_name)
            return elems["resource"]

        result = self.instance_name

        for tag in self.details.get("Tags", []):
            if tag["Key"] == "Name":
                result = tag["Value"]

        return result

    @property
    def type_info(self):
        return ""

    def __str__(self) -> str:
        return f"{self.service_name} - {self.instance_name} - {self.type_info}"

    def __hash__(self) -> int:
        return hash(str(self))

    def __eq__(self, other) -> bool:
        return (
            self.service_name == other.service_name
            and self.instance_name == other.instance_name
        )


class AutoServiceGroup(ServiceInstance):
    pass


@dataclass
class EC2Instance(ServiceInstance):
    instance_type: str

    @property
    def cost_per_month(self):
        COST_PER_TYPE = {
            "m3.medium": 0.067,
            "m4.2xlarge": 0.40,
            "m5.large": 0.096,
            "t2.2xlarge": 0.3712,
            "t2.large": 0.0928,
            "t2.medium": 0.0464,
            "t2.micro": 0.0116,
            "t2.small": 0.023,
            "t2.xlarge": 0.1856,
            "t3.small": 0.0208,
            "t3a.large": 0.0752,
            "t3a.xlarge": 0.1504,
        }
        return COST_PER_TYPE.get(self.instance_type, 0.00) * 24 * 30

    @property
    def type_info(self):
        return self.instance_type


class EksCluster(ServiceInstance):
    @property
    def name(self):
        return self.details["name"]

    @property
    def label(self):
        return self.name


class HostedZone(ServiceInstance):
    @property
    def name(self):
        return self.details["Name"]

    @property
    def label(self):
        return self.name


class LambdaFunction(ServiceInstance):
    @property
    def name(self):
        return self.details["FunctionName"]

    @property
    def label(self):
        return self.name


class LoadBalancer(ServiceInstance):
    @property
    def cost_per_month(self):
        return 0.025 * 24 * 30

    @property
    def name(self):
        return self.details["LoadBalancerName"]

    @property
    def label(self):
        return self.name


class NatGateway(ServiceInstance):
    @property
    def cost_per_month(self):
        return 0.045 * 24 * 30


class NetworkInterface(ServiceInstance):
    @property
    def label(self):
        result = super().label

        ip = self.address
        if ip:
            result += "\\n" + ip

        return result

    @property
    def cost_per_month(self):
        if self.details.get("Association", {}).get("PublicIp", None):
            return 0.005 * 24 * 30

        return 0

    @property
    def address(self):
        public = self.details.get("Association", {}).get("PublicIp", None)
        if public:
            return public

        private = self.details.get("PrivateIpAddress", None)
        if private:
            return private

        return None


@dataclass
class RdsInstance(ServiceInstance):
    instance_type: str

    @property
    def cost_per_month(self):
        COST_PER_TYPE = {
            "db.m3.2xlarge": 1.55,
            "db.m5.2xlarge": 0.712,
            "db.m5.xl": 0.356,
            "db.m5.xlarge": 0.342,
            "db.r5.large": 0.25,
            "db.r5.xl": 0.50,
            "db.r5.xlarge": 0.48,
            "db.t2.medium": 0.073,
            "db.t2.micro": 0.017,
            "db.t2.small": 0.036,
            "db.t3.medium": 0.072,
            "db.t3.small": 0.036,
        }
        return COST_PER_TYPE.get(self.instance_type, 0.00) * 24 * 30

    @property
    def type_info(self):
        return self.instance_type


class SecurityGroup(ServiceInstance):
    @property
    def name(self):
        return self.details["GroupName"]

    @property
    def label(self):
        return self.name

    @property
    def relationships(self) -> Tuple[str, str]:
        me = self.details["GroupId"]

        for ingress in self.details["IpPermissions"]:
            for pairs in ingress["UserIdGroupPairs"]:
                for attr in ["GroupId", "VpcPeeringConnectionId"]:
                    value = pairs.get(attr, None)
                    if value:
                        yield (value, me)

        for egress in self.details["IpPermissionsEgress"]:
            for pairs in egress["UserIdGroupPairs"]:
                for attr in ["GroupId", "VpcPeeringConnectionId"]:
                    value = pairs.get(attr, None)
                    if value:
                        yield (me, value)


class Subnet(ServiceInstance):
    @property
    def cidr(self):
        return self.details["CidrBlock"]

    @property
    def subnet_type(self):
        if self.details["MapPublicIpOnLaunch"]:
            return "Public"
        return "Private"


class TargetGroup(ServiceInstance):
    @property
    def name(self):
        result = self.details["TargetGroupName"]

        for tag in self.details.get("Tags", []):
            if tag["Key"] == "Name":
                result = tag["Value"]

        return result

    @property
    def label(self):
        return self.name

    @property
    def type_info(self):
        return self.details["TargetType"]


class VpcEndpoint(ServiceInstance):
    @classmethod
    def create(cls, details: dict, instance_name: str):
        instance_type = details["VpcEndpointType"]
        if instance_type == "Interface":
            service_name = "EPT-I"
        elif instance_type == "Gateway":
            service_name = "EPT-GW"
        elif instance_type == "GatewayLoadBalancer":
            service_name = "EPT-GWLB"
        else:
            logging.error("Unrecognized type %s", instance_type)

        return VpcEndpoint(details, service_name, instance_name)

    @property
    def name(self):
        return self.details["ServiceName"]

    @property
    def label(self):
        return self.name

    @property
    def type_info(self):
        return self.details["VpcEndpointType"]
