# Unit tests for the cost models added alongside the new AWS scanners
# (EBS, ElastiCache, EFS, Transit Gateway). These are pure-logic tests: they
# construct service instances directly and assert on cost/type_info, so they
# need no AWS calls and no wand fixtures.

import unittest

from src.service import (
    EbsVolume,
    EfsFileSystem,
    ElastiCacheCluster,
    TransitGateway,
)


class TestEbsVolumeCost(unittest.TestCase):
    """Cost is the per-GB rate for the volume type times provisioned size."""

    def test_gp3_cost_from_type_and_size(self):
        vol = EbsVolume({}, "EBS", "vol-1", volume_type="gp3", size_gb=100)
        self.assertAlmostEqual(vol.cost_per_month, 100 * 0.08)

    def test_io2_uses_io2_rate(self):
        vol = EbsVolume({}, "EBS", "vol-1", volume_type="io2", size_gb=50)
        self.assertAlmostEqual(vol.cost_per_month, 50 * 0.125)

    def test_unknown_type_is_free(self):
        vol = EbsVolume({}, "EBS", "vol-1", volume_type="mystery", size_gb=100)
        self.assertEqual(vol.cost_per_month, 0)

    def test_type_info_reports_volume_type(self):
        vol = EbsVolume({}, "EBS", "vol-1", volume_type="st1", size_gb=500)
        self.assertEqual(vol.type_info, "st1")


class TestElastiCacheClusterCost(unittest.TestCase):
    """Cost scales with node type rate and the number of nodes."""

    def test_cost_scales_with_node_count(self):
        cache = ElastiCacheCluster(
            {}, "CACHE", "c1", instance_type="cache.t3.medium", num_nodes=2
        )
        self.assertAlmostEqual(cache.cost_per_month, 0.068 * 2 * 24 * 30)

    def test_unknown_node_type_is_free(self):
        cache = ElastiCacheCluster(
            {}, "CACHE", "c1", instance_type="cache.unknown", num_nodes=3
        )
        self.assertEqual(cache.cost_per_month, 0)

    def test_name_prefers_cluster_id_from_details(self):
        cache = ElastiCacheCluster(
            {"CacheClusterId": "prod-redis"}, "CACHE", "c1", instance_type="cache.m5.large"
        )
        self.assertEqual(cache.name, "prod-redis")

    def test_type_info_reports_node_type(self):
        cache = ElastiCacheCluster({}, "CACHE", "c1", instance_type="cache.r5.large")
        self.assertEqual(cache.type_info, "cache.r5.large")


class TestEfsFileSystemCost(unittest.TestCase):
    """Cost is the per-GB tier rate times the stored size."""

    def test_standard_tier_cost(self):
        efs = EfsFileSystem({}, "EFS", "fs-1", size_gb=500.0)
        self.assertAlmostEqual(efs.cost_per_month, 500.0 * 0.30)

    def test_infrequent_access_tier_cost(self):
        efs = EfsFileSystem(
            {}, "EFS", "fs-1", size_gb=1000.0, tier="InfrequentAccessStorage"
        )
        self.assertAlmostEqual(efs.cost_per_month, 1000.0 * 0.016)

    def test_name_prefers_name_tag(self):
        efs = EfsFileSystem(
            {"Tags": [{"Key": "Name", "Value": "shared-fs"}]}, "EFS", "fs-1"
        )
        self.assertEqual(efs.name, "shared-fs")

    def test_cost_defaults_to_zero_without_size(self):
        efs = EfsFileSystem({}, "EFS", "fs-1")
        self.assertEqual(efs.cost_per_month, 0)


class TestTransitGatewayCost(unittest.TestCase):
    """Each VPC attachment carries a flat hourly attachment charge."""

    def test_attachment_hourly_cost(self):
        tgw = TransitGateway({}, "TGW", "tgw-1")
        self.assertAlmostEqual(tgw.cost_per_month, 0.05 * 24 * 30)


if __name__ == "__main__":
    unittest.main()
