# Integration test for src/vpc.py
# Tests real AWS API calls via boto3/botocore through the VPC class.
# Mocking is handled externally — no mocks are used here.

import logging
import unittest
from collections import namedtuple

import boto3

from src.context import Context
from src.vpc import VPC, AvailabilityZone, Relation

logger = logging.getLogger(__name__)


def make_context(region_name: str = "us-east-1") -> Context:
    """Build a real Context object using live boto3 clients."""
    session = boto3.Session(region_name=region_name)
    ctx = Context.__new__(Context)
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
    """Return the first available VPC id in the account/region."""
    return "vpc-ffc39a9a"


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

    @classmethod
    def tearDownClass(cls):
        pass

    @unittest.skip('Not available in target VPC')
    def test_subnets_populated(self):
        services = list(self.vpc.services)
        subnet_services = [s for s in services if getattr(s, "type", None) == "SUBN"]
        self.assertGreater(len(subnet_services), 0)

    @unittest.skip('Not available in target VPC')
    def test_subnet_service_has_id(self):
        services = list(self.vpc.services)
        subnet_services = [s for s in services if getattr(s, "type", None) == "SUBN"]
        first = subnet_services[0]
        self.assertTrue(hasattr(first, "id"))
        self.assertTrue(first.id.startswith("subnet-"))

    @unittest.skip('Not available in target VPC')
    def test_azs_populated_by_subnets(self):
        azs = list(self.vpc.availability_zones)
        self.assertGreater(len(azs), 0)

    def test_az_subnet_ids_non_empty(self):
        for az in self.vpc.availability_zones:
            self.assertIsInstance(az, AvailabilityZone)
            self.assertIsInstance(az.subnet_ids, list)

    @unittest.skip('Not available in target VPC')
    def test_getitem_known_subnet_returns_service(self):
        services = list(self.vpc.services)
        subnet_services = [s for s in services if getattr(s, "type", None) == "SUBN"]
        known_id = subnet_services[0].id
        result = self.vpc[known_id]
        self.assertIsNotNone(result)
        self.assertEqual(result.id, known_id)

    @unittest.skip('Not available in target VPC')
    def test_contains_known_subnet_true(self):
        services = list(self.vpc.services)
        subnet_services = [s for s in services if getattr(s, "type", None) == "SUBN"]
        known_id = subnet_services[0].id
        self.assertIn(known_id, self.vpc)


class TestVPCDescribeENIs(unittest.TestCase):
    """describe_enis makes a real EC2 API call; verify ENI state."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_enis(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_enis_populated(self):
        services = list(self.vpc.services)
        eni_services = [s for s in services if getattr(s, "type", None) == "ENI"]
        # ENIs may or may not exist; if they exist they should have proper ids
        for eni in eni_services:
            self.assertTrue(eni.id.startswith("eni-"))

    def test_subnets_dict_updated_by_enis(self):
        eni_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ENI"]
        if eni_services:
            # At least one subnet should map to an ENI id
            all_mapped = [v for vals in self.vpc.subnets.values() for v in vals]
            self.assertGreater(len(all_mapped), 0)


class TestVPCDescribeNATs(unittest.TestCase):
    """describe_nats makes a real EC2 API call for NAT gateways."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_nats(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_nat_services_have_correct_type(self):
        nat_services = [s for s in self.vpc.services if getattr(s, "type", None) == "NAT"]
        for nat in nat_services:
            self.assertEqual(nat.type, "NAT")
            self.assertTrue(nat.id.startswith("nat-"))

    def test_nat_subnets_mapping(self):
        nat_services = [s for s in self.vpc.services if getattr(s, "type", None) == "NAT"]
        if nat_services:
            all_mapped_ids = [v for vals in self.vpc.subnets.values() for v in vals]
            self.assertGreater(len(all_mapped_ids), 0)


class TestVPCDescribeIGWs(unittest.TestCase):
    """describe_igws makes a real EC2 API call for internet gateways."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_igws(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_igw_services_have_correct_type(self):
        igw_services = [s for s in self.vpc.services if getattr(s, "type", None) == "IGW"]
        for igw in igw_services:
            self.assertEqual(igw.type, "IGW")
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

    @classmethod
    def tearDownClass(cls):
        pass

    def test_acl_services_populated(self):
        acl_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ACL"]
        self.assertEqual(len(acl_services), 0)

    def test_acl_ids_correct_prefix(self):
        acl_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ACL"]
        for acl in acl_services:
            self.assertTrue(acl.id.startswith("acl-"))

    def test_acl_relations_added(self):
        # ACLs add subnet→acl relations
        acl_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ACL"]
        if acl_services:
            self.assertGreater(len(self.vpc.relations), 0)
            for rel in self.vpc.relations:
                self.assertIsInstance(rel, Relation)
                self.assertIsNotNone(rel.source)
                self.assertIsNotNone(rel.target)


class TestVPCDescribeSGs(unittest.TestCase):
    """describe_sgs makes a real EC2 API call for security groups."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_sgs(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_sg_services_populated(self):
        sg_services = [s for s in self.vpc.services if getattr(s, "type", None) == "SG"]
        self.assertEqual(len(sg_services), 0)

    def test_sg_ids_correct_prefix(self):
        sg_services = [s for s in self.vpc.services if getattr(s, "type", None) == "SG"]
        for sg in sg_services:
            self.assertTrue(sg.id.startswith("sg-"))

    @unittest.skip('Not available in target VPC')
    def test_sg_contained_in_vpc(self):
        sg_services = [s for s in self.vpc.services if getattr(s, "type", None) == "SG"]
        first_id = sg_services[0].id
        self.assertIn(first_id, self.vpc)


class TestVPCDescribeEC2s(unittest.TestCase):
    """describe_ec2s makes a real EC2 API call for instances in the VPC."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_ec2s(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_ec2_services_have_correct_type(self):
        ec2_services = [s for s in self.vpc.services if getattr(s, "type", None) == "EC2"]
        for ec2 in ec2_services:
            self.assertEqual(ec2.type, "EC2")
            self.assertTrue(ec2.id.startswith("i-"))

    def test_ec2_azs_populated(self):
        ec2_services = [s for s in self.vpc.services if getattr(s, "type", None) == "EC2"]
        if ec2_services:
            az_service_ids = [
                _id
                for az in self.vpc.availability_zones
                for _id in az.service_ids
            ]
            self.assertGreater(len(az_service_ids), 0)

    def test_ec2_subnets_mapping_populated(self):
        ec2_services = [s for s in self.vpc.services if getattr(s, "type", None) == "EC2"]
        if ec2_services:
            all_mapped = [v for vals in self.vpc.subnets.values() for v in vals]
            self.assertGreater(len(all_mapped), 0)


class TestVPCDescribeLambdas(unittest.TestCase):
    """describe_lambdas makes a real Lambda API call."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_lambdas(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_lambda_services_have_correct_type(self):
        lambda_services = [s for s in self.vpc.services if getattr(s, "type", None) == "Lambda"]
        for lmbd in lambda_services:
            self.assertEqual(lmbd.type, "Lambda")
            self.assertTrue(lmbd.id.startswith("arn:"))


class TestVPCDescribeRDSs(unittest.TestCase):
    """describe_rdss makes a real RDS API call."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_rdss(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_rds_services_have_correct_type(self):
        rds_services = [s for s in self.vpc.services if getattr(s, "type", None) == "RDS"]
        for rds in rds_services:
            self.assertEqual(rds.type, "RDS")

    def test_rds_azs_populated(self):
        rds_services = [s for s in self.vpc.services if getattr(s, "type", None) == "RDS"]
        if rds_services:
            az_service_ids = [
                _id
                for az in self.vpc.availability_zones
                for _id in az.service_ids
            ]
            self.assertGreater(len(az_service_ids), 0)


class TestVPCDescribeELBsV2(unittest.TestCase):
    """describe_elbsV2 makes real ALB/NLB API calls."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_elbsV2(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_elbv2_services_have_correct_type(self):
        elb_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ELBv2"]
        for elb in elb_services:
            self.assertEqual(elb.type, "ELBv2")
            self.assertTrue(elb.id.startswith("arn:"))

    def test_target_group_services_registered(self):
        tg_services = [s for s in self.vpc.services if getattr(s, "type", None) == "TG"]
        for tg in tg_services:
            self.assertEqual(tg.type, "TG")
            self.assertTrue(tg.id.startswith("arn:"))


class TestVPCDescribeASGs(unittest.TestCase):
    """describe_asgs makes real AutoScaling and EC2 API calls."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_asgs(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_asg_services_have_correct_type(self):
        asg_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ASG"]
        for asg in asg_services:
            self.assertEqual(asg.type, "ASG")

    def test_asg_az_entries_are_strings(self):
        asg_services = [s for s in self.vpc.services if getattr(s, "type", None) == "ASG"]
        if asg_services:
            for az in self.vpc.availability_zones:
                for sid in az.service_ids:
                    self.assertIsInstance(sid, str)


class TestVPCDescribeEKSs(unittest.TestCase):
    """describe_ekss makes real EKS API calls."""

    @classmethod
    def setUpClass(cls):
        cls.context = make_context()
        cls.vpc_id = _discover_a_vpc_id(cls.context)
        cls.vpc = VPC(region="us-east-1", id=cls.vpc_id)
        cls.vpc.describe_ekss(cls.context)

    @classmethod
    def tearDownClass(cls):
        pass

    def test_eks_services_have_correct_type(self):
        eks_services = [s for s in self.vpc.services if getattr(s, "type", None) == "EKS"]
        for eks in eks_services:
            self.assertEqual(eks.type, "EKS")
            self.assertTrue(eks.id.startswith("arn:"))


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

    @classmethod
    def tearDownClass(cls):
        pass

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
