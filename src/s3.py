import datetime
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from src.service import S3Bucket

logger = logging.getLogger()

# S3 is a global service, but a client still needs a region to sign requests;
# list_buckets and get_bucket_location work account-wide from any region.
S3_GLOBAL_REGION = "us-east-1"


def scan_all_buckets(session) -> Dict[str, List[S3Bucket]]:
    """List every bucket in the account once and group them by home region.

    S3 is account-global, so listing/locating buckets is done a single time
    here rather than once per region. Each bucket is enriched with its dominant
    storage tier and size from its home region's CloudWatch. The scan is
    best-effort: any AWS failure logs and is skipped rather than aborting.

    Returns ``{region: [S3Bucket, ...]}`` for distribution to each Region.
    """
    by_region: Dict[str, List[S3Bucket]] = defaultdict(list)

    s3 = session.client("s3", region_name=S3_GLOBAL_REGION)
    try:
        buckets = s3.list_buckets()["Buckets"]
    except Exception as e:
        logger.warning("Could not list S3 buckets: %s", e)
        return by_region

    cloudwatch_clients: Dict[str, any] = {}
    for bucket in buckets:
        name = bucket["Name"]
        region = bucket_region(s3, name)
        if region is None:
            continue

        cw = cloudwatch_clients.get(region)
        if cw is None:
            cw = session.client("cloudwatch", region_name=region)
            cloudwatch_clients[region] = cw

        tier, size_gb = bucket_storage(cw, name)
        by_region[region].append(S3Bucket(bucket, "S3", name, tier, size_gb))

    return by_region


def bucket_region(s3_client, name: str) -> Optional[str]:
    """The bucket's home region, or None if it can't be determined.

    ``get_bucket_location`` reports us-east-1 as ``None`` and legacy eu-west-1
    as ``EU``; both are normalized to real region names.
    """
    try:
        loc = s3_client.get_bucket_location(Bucket=name)["LocationConstraint"]
    except Exception as e:
        logger.warning("Could not get location for bucket %s: %s", name, e)
        return None

    region = loc or "us-east-1"
    if region == "EU":
        region = "eu-west-1"

    return region


def bucket_storage(cloudwatch_client, name: str) -> Tuple[str, float]:
    """Best-effort (tier, size_gb) for a bucket from CloudWatch.

    S3 publishes ``BucketSizeBytes`` once per day, split by ``StorageType``. A
    bucket may hold several storage classes; we report the largest one, whose
    tier drives the cost estimate. On any failure (no metrics published yet,
    missing cloudwatch permissions, throttling) the bucket falls back to a
    zero-cost default rather than aborting the scan.
    """
    GB = 1024**3
    default = ("StandardStorage", 0.0)

    try:
        metrics = cloudwatch_client.list_metrics(
            Namespace="AWS/S3",
            MetricName="BucketSizeBytes",
            Dimensions=[{"Name": "BucketName", "Value": name}],
        )["Metrics"]

        # Timestamps are passed as ISO strings so the request stays
        # JSON-serializable for the test-fixture hash.
        end = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
        start = end - datetime.timedelta(days=2)

        best_tier, best_bytes = default[0], 0.0
        for metric in metrics:
            storage_type = next(
                (
                    d["Value"]
                    for d in metric["Dimensions"]
                    if d["Name"] == "StorageType"
                ),
                None,
            )
            if storage_type is None:
                continue

            points = cloudwatch_client.get_metric_statistics(
                Namespace="AWS/S3",
                MetricName="BucketSizeBytes",
                Dimensions=[
                    {"Name": "BucketName", "Value": name},
                    {"Name": "StorageType", "Value": storage_type},
                ],
                StartTime=start.isoformat(),
                EndTime=end.isoformat(),
                Period=86400,
                Statistics=["Average"],
            )["Datapoints"]

            if not points:
                continue

            latest = max(points, key=lambda p: p["Timestamp"])["Average"]
            if latest > best_bytes:
                best_bytes, best_tier = latest, storage_type

        return best_tier, best_bytes / GB
    except Exception as e:
        logger.warning("Could not read S3 metrics for %s: %s", name, e)
        return default
