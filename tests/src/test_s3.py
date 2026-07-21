# Integration test for src/s3.py
# Exercises the account-global S3 scan via real boto3/botocore calls through
# the wand replay bridge. Mocking is handled externally — no mocks here.
# Assertions are structural (not tied to specific bucket names/regions) so the
# suite survives a re-capture against any account.

import unittest

import boto3

from src.s3 import bucket_region, scan_all_buckets
from src.service import S3Bucket


def _discover_a_bucket(s3) -> str:
    """First bucket name in the account, or None if there are none."""
    buckets = s3.list_buckets()["Buckets"]
    return buckets[0]["Name"] if buckets else None


class TestScanAllBuckets(unittest.TestCase):
    """scan_all_buckets lists every bucket once and groups them by home region."""

    @classmethod
    def setUpClass(cls):
        session = boto3.Session(region_name="us-east-1")
        cls.by_region = scan_all_buckets(session)

    def test_returns_s3_bucket_instances(self):
        for buckets in self.by_region.values():
            for bucket in buckets:
                self.assertIsInstance(bucket, S3Bucket)
                self.assertEqual(bucket.service_name, "S3")

    def test_grouped_under_non_empty_region_keys(self):
        for region in self.by_region:
            self.assertIsInstance(region, str)
            self.assertTrue(region)

    def test_every_bucket_has_a_tier_and_non_negative_cost(self):
        for buckets in self.by_region.values():
            for bucket in buckets:
                self.assertTrue(bucket.type_info)
                self.assertGreaterEqual(bucket.cost_per_month, 0)


class TestBucketRegion(unittest.TestCase):
    """bucket_region resolves a bucket's home region from get_bucket_location."""

    @classmethod
    def setUpClass(cls):
        session = boto3.Session(region_name="us-east-1")
        cls.s3 = session.client("s3", region_name="us-east-1")
        cls.name = _discover_a_bucket(cls.s3)

    def test_region_resolves_to_a_non_empty_region(self):
        if self.name is None:
            self.skipTest("no S3 buckets in account")
        region = bucket_region(self.s3, self.name)
        self.assertIsInstance(region, str)
        self.assertTrue(region)


class TestS3BucketCost(unittest.TestCase):
    """Cost is derived from the storage tier rate and stored size."""

    def test_cost_from_tier_and_size(self):
        bucket = S3Bucket(
            {"Name": "b"}, "S3", "b", tier="StandardStorage", size_gb=100.0
        )
        # 100 GB of Standard at 0.023 $/GB-month
        self.assertAlmostEqual(bucket.cost_per_month, 2.3)

    def test_glacier_tier_uses_glacier_rate(self):
        bucket = S3Bucket(
            {"Name": "b"}, "S3", "b", tier="GlacierStorage", size_gb=1024.0
        )
        self.assertAlmostEqual(bucket.cost_per_month, 1024 * 0.0036)

    def test_cost_defaults_to_zero_without_size(self):
        bucket = S3Bucket({"Name": "b"}, "S3", "b")
        self.assertEqual(bucket.cost_per_month, 0)

    def test_type_info_reports_tier(self):
        bucket = S3Bucket({"Name": "b"}, "S3", "b", tier="OneZoneIAStorage")
        self.assertEqual(bucket.type_info, "OneZoneIAStorage")


if __name__ == "__main__":
    unittest.main()
