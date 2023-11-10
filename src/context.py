from dataclasses import dataclass


@dataclass
class Context:
    user: str
    account: str
    profile: str
    options: any
    region: str
    vpc_client: any
    elbV2_client: any
    elb_client: any
    lambda_client: any
    eks_client: any
    asg_client: any
    rds_client: any
    ec2: any
    route53: any


def build_context(session, region: str, options: any) -> Context:
    identity = session.client("sts").get_caller_identity()

    return Context(
        identity["UserId"],
        identity["Account"],
        session.profile_name,
        options,
        region,
        session.client("ec2", region_name=region),
        session.client("elbv2", region_name=region),
        session.client("elb", region_name=region),
        session.client("lambda", region_name=region),
        session.client("eks", region_name=region),
        session.client("autoscaling", region_name=region),
        session.client("rds", region_name=region),
        session.resource("ec2", region_name=region),
        session.client("route53", region_name=region),
    )
