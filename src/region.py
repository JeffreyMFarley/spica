import io
import logging
import os
from typing import List

from src.context import build_context
from src.outputs.graphviz import to_graphviz, render
from src.vpc import VPC


ALL_REGIONS = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
    "us-gov-east-1",
    "us-gov-west-1",
    "af-south-1",
    "ap-east-1",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-south-1",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-southeast-3",
    "ca-central-1",
    "cn-north-1",
    "cn-northwest-1",
    "eu-central-1",
    "eu-north-1",
    "eu-south-1",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "me-south-1",
    "sa-east-1",
]


US_REGIONS = [
    "us-east-1",
    "us-east-2",
    "us-west-1",
    "us-west-2",
]

FIX_FILE_NAME = str.maketrans(
    {
        " ": "-",
        "\\": "_",
        "/": "_",
        ".": None,
    }
)


class Region:
    def __init__(self, session, region: str, options: any) -> None:
        self.name: str = region
        self.context = build_context(session, region, options)
        self.vpcs: List[VPC] = []

    def add_vpc(self, vpc_id):
        vpc = VPC(self, vpc_id)
        self.vpcs.append(vpc)
        vpc.scan(self.context)

    def to_csv(self, prefix, stream):
        fwd = f"{prefix}{self.context.profile}\t{self.name}\t"
        for v in self.vpcs:
            v.to_csv(fwd, stream)

    def to_graphviz(self, directory, cost_threshhold: float):
        for v in self.vpcs:
            if v.cost_per_month() < cost_threshhold:
                logging.warn("%s is below the cost threshhold. Skipping.", v.name)
                continue

            full_dir = os.path.join(directory, self.context.account)
            file_title = f"{v.id}_{v.name}".translate(FIX_FILE_NAME)

            if not os.path.exists(full_dir):
                os.mkdir(full_dir)
            full_path = os.path.join(full_dir, f"{file_title}.gv")

            with io.open(full_path, "w") as f:
                to_graphviz(v, f)
                f.flush()

            render(full_path)
