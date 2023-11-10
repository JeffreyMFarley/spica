import io
import logging
import sys

import boto3
import configargparse
from botocore.exceptions import ClientError, ProfileNotFound
from src.region import Region, US_REGIONS, ALL_REGIONS

# logger config
logger = logging.getLogger()
logging.basicConfig(level=logging.INFO, format="%(message)s")

# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------


def build_arg_parser():
    p = configargparse.ArgParser(
        prog="spica", description="scan AWS for VPC resources and price them"
    )
    p.add("-v", "--vpc", help="Scan a single VPC to scan")
    p.add("-r", "--region", help="AWS region that the VPC resides in")
    p.add(
        "-p",
        "--profile",
        default="default",
        help="The AWS profile to use when scanning",
    )
    g = p.add_argument_group("Options")
    g.add(
        "--all-regions",
        action="store_true",
        help="scan all of the AWS regions, not just US",
    )
    g.add(
        "--ignore-free-resources",
        action="store_true",
        help="do not scan for no cost resources (Security Groups, Route Tables, etc)",
    )
    g.add(
        "--cost-threshhold",
        type=float,
        default=2.0,
        help="the minimum cost of a VPC before outputting",
    )
    g = p.add_argument_group("Output")
    g.add("--output-csv", help="write the information in CSV format to this file")
    g.add(
        "--output-gv", help="write the information in Graphviz format to this directory"
    )
    return p


if __name__ == "__main__":
    p = build_arg_parser()
    args = p.parse_args()

    try:
        session = boto3.Session(profile_name=args.profile)
    except ProfileNotFound as e:
        logger.warning("{}, please provide a valid AWS profile name".format(e))
        exit(-1)

    regions = []

    if args.vpc:
        region = Region(session, args.region, args)
        regions.append(region)

        region.add_vpc(args.vpc)
    else:
        to_scan = ALL_REGIONS if args.all_regions else US_REGIONS
        if args.region:
            to_scan = [args.region]

        for reg in to_scan:
            clear = " " * 80
            try:
                region = Region(session, reg, args)
                regions.append(region)

                vpcs = list(region.context.ec2.vpcs.filter(Filters=[]))
                for vpc in vpcs:
                    sys.stdout.write(
                        f"\r{clear}\rScanning region: {reg}\tVPC: {vpc.id}"
                    )
                    region.add_vpc(vpc.id)

            except ClientError as e:
                sys.stdout.write(f"\r{clear}\r{reg}\t{e.response['Error']['Message']}")
        sys.stdout.write(f"\r{clear}\rFinished scanning\n")

    if args.output_csv:
        with io.open(args.output_csv, "w") as f:
            for r in regions:
                r.to_csv("", f)
                f.flush()
    elif args.output_gv:
        for r in regions:
            r.to_graphviz(args.output_gv, args.cost_threshhold)
    else:
        for r in regions:
            r.to_csv("", sys.stdout)
            sys.stdout.flush()
