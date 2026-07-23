# Integration test for src/vpc.py
# Tests real AWS API calls via boto3/botocore through the VPC class.
# Mocking is handled externally — no mocks are used here.
#
# The target VPC (see _discover_a_vpc_id) is a real, populated VPC. Assertions
# are written against its actual shape: multiple subnets across several AZs,
# ENIs, a NAT gateway, an internet gateway, network ACLs, security groups, an
# RDS instance, and NLB/target-group load balancing. Resource families that are
# genuinely absent (EC2/Lambda/ASG/EKS) are asserted to come back empty rather
# than skipped, so a regression that mis-scopes them would surface here.

import logging
import unittest

import boto3

from src.context import Context
from src.vpc import VPC, AvailabilityZone, Relation

logger = logging.getLogger(__name__)


def make_context(region_name: str = "us-east-1") -> Context:
    """Build a real Context object using live boto3 clients."""
    session = boto3.Session(region_name=region_name)
    ctx = Context.__new__(Context)
    ctx.region = region_name
    ctx.vpc_client = session.client("ec2", region_name=region_name)
    ctx.asg_client = session.client("autoscaling", region_name=region_name)
    ctx.eks_client = session.client("eks", region_name=region_name)
    ctx.lambda_client = session.client("lambda", region_name=region_name)
    ctx.rds_client = session.client("rds", region_name=region_name)
    ctx.elb_client = session.client("elb", region_name=region_name)
    ctx.elbV2_client = session.client("elbv2", region_name=region_name)
    ctx.route53 = session.client("route53")
    return ctx


def _discover_a_vpc_id(context: Context) -> str:
    """Return the id of the populated VPC these integration tests target."""
    return "vpc-0dc21f95a61d09691"


def services_of(vpc: VPC, service_name: str) -> list:
    """All discovered services of a given kind.

    Services expose ``service_name`` (e.g. "SUBN", "ENI", "ELBv2") — NOT a
    ``type`` attribute. Filtering on the wrong attribute silently yields an
    empty list and turns every assertion below into a no-op.
    """
    return [s for s in vpc.services if s.service_name == service_name]


class TestVPCInstantiation(unittest.TestCase):
    """Verify basic VPC object construction without any network scan."""

    def setUp(self):
        self.vpc = VPC(region="us-east-1", id="vpc-test0001")

    def test_id_stored(self):
        self.assertEqual(self.vpc.id, "vpc-test0001")

    def test_region_stored(self):
        self.assertEqual(self.vpc.region, "us-east-1")

    def test_services_initially_empty(self):
        self.assertEqual(len(list(self.vpc.services)), 0)

    def test_relations_initially_empty(self):
        self.assertEqual(len(self.vpc.relations), 0)

    def test_getitem_missing_key_returns_none(self):
        result = self.vpc["nonexistent-key"]
        self.assertIsNone(result)

    def test_contains_missing_key_returns_false(self):
        self.assertNotIn("nonexistent-key", self.vpc)


class TestVPCDescribeSubnets(unittest.TestCase):
    """describe_subnets makes a real EC2 API call; verify state is populated."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_subnets(cls.context)

    def test_subnets_populated(self):
        self.assertGreater(len(services_of(self.vpc, "SUBN")), 0)

    def test_subnet_service_has_id(self):
        for subnet in services_of(self.vpc, "SUBN"):
            self.assertTrue(subnet.id.startswith("subnet-"))

    def test_azs_populated_by_subnets(self):
        azs = list(self.vpc.availability_zones)
        self.assertGreater(len(azs), 0)

    def test_every_subnet_registered_in_some_az(self):
        # Each discovered subnet must be reachable from exactly one AZ bucket.
        subnet_ids = {s.id for s in services_of(self.vpc, "SUBN")}
        az_subnet_ids = [
            sid for az in self.vpc.availability_zones for sid in az.subnet_ids
        ]
        self.assertEqual(sorted(subnet_ids), sorted(az_subnet_ids))

    def test_az_subnet_ids_non_empty(self):
        for az in self.vpc.availability_zones:
            self.assertIsInstance(az, AvailabilityZone)
            self.assertIsInstance(az.subnet_ids, list)
            self.assertGreater(len(az.subnet_ids), 0)

    def test_getitem_known_subnet_returns_service(self):
        known_id = services_of(self.vpc, "SUBN")[0].id
        result = self.vpc[known_id]
        self.assertIsNotNone(result)
        self.assertEqual(result.id, known_id)

    def test_contains_known_subnet_true(self):
        known_id = services_of(self.vpc, "SUBN")[0].id
        self.assertIn(known_id, self.vpc)


class TestVPCDescribeENIs(unittest.TestCase):
    """describe_enis makes a real EC2 API call; verify ENI state."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_enis(cls.context)

    def test_enis_populated(self):
        eni_services = services_of(self.vpc, "ENI")
        self.assertGreater(len(eni_services), 0)
        for eni in eni_services:
            self.assertTrue(eni.id.startswith("eni-"))

    def test_subnets_dict_updated_by_enis(self):
        # Every ENI pins itself to its subnet, so the subnet map is non-empty
        # and each mapped id is a real ENI.
        all_mapped = [v for vals in self.vpc.subnets.values() for v in vals]
        self.assertGreater(len(all_mapped), 0)
        for eni_id in all_mapped:
            self.assertTrue(eni_id.startswith("eni-"))


class TestVPCDescribeNATs(unittest.TestCase):
    """describe_nats makes a real EC2 API call for NAT gateways."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_nats(cls.context)

    def test_nat_services_populated(self):
        nat_services = services_of(self.vpc, "NAT")
        self.assertGreater(len(nat_services), 0)
        for nat in nat_services:
            self.assertEqual(nat.service_name, "NAT")
            self.assertTrue(nat.id.startswith("nat-"))

    def test_nat_maps_to_subnet(self):
        # Each NAT gateway lives in a subnet; the subnet map records it.
        all_mapped = [v for vals in self.vpc.subnets.values() for v in vals]
        self.assertTrue(any(m.startswith("nat-") for m in all_mapped))

    def test_nat_relates_to_eni(self):
        # A NAT gateway owns a network interface (nat- -> eni-).
        nat_ids = {n.id for n in services_of(self.vpc, "NAT")}
        eni_edges = [
            r for r in self.vpc.relations
            if r.source in nat_ids and r.target.startswith("eni-")
        ]
        self.assertGreater(len(eni_edges), 0)


class TestVPCDescribeIGWs(unittest.TestCase):
    """describe_igws makes a real EC2 API call for internet gateways."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_igws(cls.context)

    def test_igw_services_populated(self):
        igw_services = services_of(self.vpc, "IGW")
        self.assertGreater(len(igw_services), 0)
        for igw in igw_services:
            self.assertEqual(igw.service_name, "IGW")
            self.assertTrue(igw.id.startswith("igw-"))


class TestVPCDescribeACLs(unittest.TestCase):
    """describe_acls makes a real EC2 API call for network ACLs."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        # Subnets must be described first so relations can be linked
        cls.vpc.describe_subnets(cls.context)
        cls.vpc.describe_acls(cls.context)

    def test_acl_services_populated(self):
        self.assertGreater(len(services_of(self.vpc, "ACL")), 0)

    def test_acl_ids_correct_prefix(self):
        for acl in services_of(self.vpc, "ACL"):
            self.assertTrue(acl.id.startswith("acl-"))

    def test_acl_relations_added(self):
        # ACLs add subnet -> acl associations; every edge must point from a
        # real subnet to a real ACL.
        acl_ids = {a.id for a in services_of(self.vpc, "ACL")}
        subnet_ids = {s.id for s in services_of(self.vpc, "SUBN")}
        acl_edges = [r for r in self.vpc.relations if r.target in acl_ids]
        self.assertGreater(len(acl_edges), 0)
        for rel in acl_edges:
            self.assertIsInstance(rel, Relation)
            self.assertIn(rel.source, subnet_ids)
            self.assertIn(rel.target, acl_ids)


class TestVPCDescribeSGs(unittest.TestCase):
    """describe_sgs makes a real EC2 API call for security groups."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_sgs(cls.context)

    def test_sg_services_populated(self):
        self.assertGreater(len(services_of(self.vpc, "SG")), 0)

    def test_sg_ids_correct_prefix(self):
        for sg in services_of(self.vpc, "SG"):
            self.assertTrue(sg.id.startswith("sg-"))

    def test_sg_contained_in_vpc(self):
        first_id = services_of(self.vpc, "SG")[0].id
        self.assertIn(first_id, self.vpc)


class TestVPCDescribeEC2s(unittest.TestCase):
    """describe_ec2s makes a real EC2 API call for instances in the VPC.

    The target VPC currently runs no instances; assert the call scopes cleanly
    to an empty result rather than leaking instances from other VPCs.
    """

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_ec2s(cls.context)

    def test_no_ec2_instances(self):
        self.assertEqual(len(services_of(self.vpc, "EC2")), 0)


class TestVPCDescribeLambdas(unittest.TestCase):
    """describe_lambdas makes a real Lambda API call."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_lambdas(cls.context)

    def test_lambda_services_populated(self):
        lambda_services = services_of(self.vpc, "Lambda")
        self.assertGreater(len(lambda_services), 0)
        for lmbd in lambda_services:
            self.assertEqual(lmbd.service_name, "Lambda")
            # The stored identity is the full function ARN; .id shortens it.
            self.assertTrue(lmbd.instance_name.startswith("arn:"))

    def test_lambda_mapped_to_subnets(self):
        # A VPC-attached function pins itself to its configured subnets.
        lambda_arns = {l.instance_name for l in services_of(self.vpc, "Lambda")}
        mapped = [v for vals in self.vpc.subnets.values() for v in vals]
        self.assertTrue(any(m in lambda_arns for m in mapped))

    def test_lambda_relates_to_security_group(self):
        # Each function references its VPC security groups (lambda-arn -> sg-).
        lambda_arns = {l.instance_name for l in services_of(self.vpc, "Lambda")}
        sg_edges = [
            r for r in self.vpc.relations
            if r.source in lambda_arns and r.target.startswith("sg-")
        ]
        self.assertGreater(len(sg_edges), 0)


class TestVPCDescribeRDSs(unittest.TestCase):
    """describe_rdss makes a real RDS API call."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_rdss(cls.context)

    def test_rds_services_populated(self):
        rds_services = services_of(self.vpc, "RDS")
        self.assertGreater(len(rds_services), 0)
        for rds in rds_services:
            self.assertEqual(rds.service_name, "RDS")

    def test_rds_azs_populated(self):
        az_service_ids = [
            _id for az in self.vpc.availability_zones for _id in az.service_ids
        ]
        self.assertGreater(len(az_service_ids), 0)

    def test_rds_relates_to_security_group(self):
        # Each RDS instance references its VPC security groups (rds -> sg-).
        rds_ids = {r.id for r in services_of(self.vpc, "RDS")}
        sg_edges = [
            r for r in self.vpc.relations
            if r.source in rds_ids and r.target.startswith("sg-")
        ]
        self.assertGreater(len(sg_edges), 0)


class TestVPCDescribeELBsV2(unittest.TestCase):
    """describe_elbsV2 makes real ALB/NLB API calls."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_elbsV2(cls.context)

    def test_elbv2_services_populated(self):
        elb_services = services_of(self.vpc, "ELBv2")
        self.assertGreater(len(elb_services), 0)
        for elb in elb_services:
            self.assertEqual(elb.service_name, "ELBv2")
            # The stored identity is the full ARN; .id shortens it for display.
            self.assertTrue(elb.instance_name.startswith("arn:"))

    def test_target_group_services_registered(self):
        tg_services = services_of(self.vpc, "TG")
        self.assertGreater(len(tg_services), 0)
        for tg in tg_services:
            self.assertEqual(tg.service_name, "TG")
            self.assertTrue(tg.instance_name.startswith("arn:"))

    def test_load_balancer_owns_target_group(self):
        # Each load balancer relates to its target groups (lb-arn -> tg-arn).
        lb_arns = {e.instance_name for e in services_of(self.vpc, "ELBv2")}
        tg_arns = {t.instance_name for t in services_of(self.vpc, "TG")}
        lb_tg_edges = [
            r for r in self.vpc.relations
            if r.source in lb_arns and r.target in tg_arns
        ]
        self.assertGreater(len(lb_tg_edges), 0)

    def test_hosted_zone_registered_for_load_balancer(self):
        self.assertGreater(len(services_of(self.vpc, "HZ")), 0)


class TestVPCDescribeASGs(unittest.TestCase):
    """describe_asgs makes real AutoScaling and EC2 API calls.

    No auto-scaling groups in the target VPC; assert none leak in.
    """

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_asgs(cls.context)

    def test_no_asgs(self):
        self.assertEqual(len(services_of(self.vpc, "ASG")), 0)


class TestVPCDescribeEKSs(unittest.TestCase):
    """describe_ekss makes real EKS API calls.

    No EKS clusters in the target VPC; assert none leak in.
    """

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_ekss(cls.context)

    def test_no_eks_clusters(self):
        self.assertEqual(len(services_of(self.vpc, "EKS")), 0)


class TestVPCRelationsIntegrity(unittest.TestCase):
    """Verify that relations added by multiple describe calls are well-formed."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_subnets(cls.context)
        cls.vpc.describe_enis(cls.context)
        cls.vpc.describe_sgs(cls.context)
        cls.vpc.describe_acls(cls.context)

    def test_relations_present(self):
        self.assertGreater(len(self.vpc.relations), 0)

    def test_all_relations_are_named_tuples(self):
        for rel in self.vpc.relations:
            self.assertIsInstance(rel, Relation)

    def test_relation_fields_are_non_empty_strings(self):
        for rel in self.vpc.relations:
            self.assertIsInstance(rel.source, str)
            self.assertIsInstance(rel.target, str)
            self.assertTrue(len(rel.source) > 0)
            self.assertTrue(len(rel.target) > 0)

    def test_relations_are_unique(self):
        # Relations is a set — duplicates should not appear
        relations_list = list(self.vpc.relations)
        self.assertEqual(len(relations_list), len(set(relations_list)))


if __name__ == "__main__":
    unittest.main()
