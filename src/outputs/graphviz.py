import os
import os.path
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from itertools import product
from typing import Dict, List, Set

from jinja2 import Environment, DictLoader
from src.service import NetworkInterface, ServiceInstance, Subnet
from src.vpc import Relation, VPC

REPLACE_TABLE = str.maketrans(
    {
        " ": "_",
        "-": "_",
        "/": "_",
        ".": None,
    }
)

SUBNET_SVC = ["EC2", "ENI", "NAT"]
BOTTOM_SVC = ["RDS", "CACHE", "EFS", "EBS"]

# The "doors" into the VPC — all the ways traffic gets in. Collected into one
# tier so the ingress story reads as a single band under the VPC header,
# instead of being scattered from level 3 to level 18.
INGRESS_SVC = ["IGW", "EIGW", "VPGW", "TGW", "PEER", "EPT-GW", "EPT-GWLB", "EPT-I"]

# Policy/rule nodes — these describe routing and access rules, not traffic
# hops, so they're kept out of the main top-down flow column.
POLICY_SVC = ["ACL", "RTB", "SG"]

# Which synthetic source a door connects to.
INET_DOORS = ["IGW", "EIGW", "PEER", "EPT-GW", "EPT-GWLB", "EPT-I"]
ONPREM_DOORS = ["VPGW", "TGW"]

LEVELS = [
    ["INET"],  # synthetic Internet / on-prem source (see to_graphviz)
    ["R53"],
    ["HZ"],
    ["VPC"],
    INGRESS_SVC,  # ingress tier: the doors in
    ["ELBv1", "ELBv2"],
    ["TG"],
    ["ASG"],
    ["EKS"],
    ["Lambda"],
    ["ACL"],
    ["SUBN"],
    ["EC2", "NAT"],
    ["ENI"],
    ["RDS", "CACHE", "EFS", "EBS"],
    ["RTB"],
    ["SG"],
    ["S3"],
]

NETWORK = [
    "ACL",
    "ELBv1",
    "ELBv2",
    "ENI",
    "EPT-I",
    "EPT-GWLB",
    "HZ",
    "IGW",
    "NAT",
    "PEER",
    "RTB",
    "SG",
    "TG",
    "TGW",
    "EIGW",
    "VPGW",
]
STORAGE = ["EPT-GW", "RDS", "CACHE", "EFS", "EBS"]
COMPUTE = ["ASG", "EC2", "Lambda", "EKS"]

TEMPLATE = """
digraph G {
    rankdir=TB;
    compound=true
    concentrate=true
    node [fontsize=10 shape=none labelloc=b imagepos=tc color=none height=1.0]
    edge [fontsize=9 color="grey70"]
    // newrank=true

    subgraph {
        {% for svc in route53_services -%}
        {{ svc }}
        {% endfor %}
    }
    {% if has_internet %}
    INET [label="Internet / on-prem" image="{{ inet_icon }}"]
    {% endif %}

    subgraph cluster_10 {
        label="{{ vpc_name }}"

        // -- Ingress tier: the doors in, pinned under the VPC header ----------
        subgraph cluster_ingress {
            label="ingress"
            labeljust=l
            style="rounded,dashed"
            color="purple3"
            rank="source"
            {% for svc in ingress_services -%}
            {{ svc }}
            {% endfor %}
        }

        subgraph cluster_91 {
            style="invis"
            {% for az in azs -%}
            subgraph cluster_{{ az.cluster_id }} {
                label="{{ az.label }}"
                style=""
                {% for sn in az.subnets -%}
                subgraph cluster_{{ sn.cluster_id }} {
                    label="{{ sn.label }}"

                    {{ sn.id | graphviz_id }} [style=invis]
                    {% for svc in sn.services -%}
                    {{ svc }}
                    {% endfor %}
                }
                {% endfor %}
                {% for svc in az.top_services -%}
                {{ svc }}
                {% endfor %}            
                {% for svc in az.bottom_services -%}
                {{ svc }}
                {% endfor %}            
            }
            {% endfor %}
        }

        subgraph { 
            {% for svc in top_services -%}
            {{ svc }}
            {% endfor %}
        }

        subgraph {
            {% for svc in bottom_services -%}
            {{ svc }}
            {% endfor %}
        }

        // -- Policy rail: rules, not hops -------------------------------------
        subgraph cluster_policy {
            style="invis"
            {% for svc in policy_services -%}
            {{ svc }}
            {% endfor %}
        }

        subgraph {
            rank="sink"
            {% for svc in s3_services -%}
            {{ svc }}
            {% endfor %}
        }
    }

    {% for svcs in svc_types.values() -%}
    // { rank=same; {% for svc in svcs %}{{ svc | graphviz_id }}; {% endfor %} }
    {% endfor %}

    {% for edge in ingress_edges -%}
    {{ edge }}
    {% endfor %}
    {% for edge in edges -%}
    {{ edge }}
    {% endfor %}
}
"""

# -----------------------------------------------------------------------------
# Helper Classes


@dataclass
class GVSN:
    cluster_id: str
    id: str
    label: str
    services: List[str] = field(default_factory=list)


@dataclass
class GVAZ:
    cluster_id: str
    label: str
    subnets: List[GVSN] = field(default_factory=list)
    top_services: List[str] = field(default_factory=list)
    bottom_services: List[str] = field(default_factory=list)


# -----------------------------------------------------------------------------
# Helper Methods


def cidr_sort(svc: Subnet):
    ip = svc.cidr.split("/")[0]
    return [int(x) for x in ip.split(".")]


def level(service_name: str) -> int:
    for l, types in enumerate(LEVELS):
        for t in types:
            if t == service_name:
                return l

    print(service_name, "is not found")
    return None


def graphviz_color(service: str) -> str:
    if service in NETWORK:
        return "purple3"
    if service in STORAGE:
        return "blue"

    # COMPUTE
    return "orange"


def graphviz_id(s: str) -> str:
    return s.translate(REPLACE_TABLE)


def graphviz_icon(service: str, instance_type: str = None) -> str:
    MAP = {
        "ACL": "ACL",
        "INET": "INET",
        "ASG": "ASG",
        "CACHE": "CACHE",
        "EBS": "EBS",
        "EC2": "EC2",
        "EFS": "EFS",
        "EIGW": "IGW",
        "EKS": "EKS",
        "ELBv1": "LB",
        "ELBv2": "ALB",
        "ENI": "ENI",
        "EPT-I": "EPT-I",
        "EPT-GW": "EPT-GW",
        "EPT-GWLB": "EPT-GWLB",
        "HZ": "HZ",
        "IGW": "IGW",
        "Lambda": "Lambda",
        "NAT": "NAT",
        "PEER": "vpc-peering",
        "RDS": "RDS",
        "RTB": "RouteTable",
        "S3": "S3",
        "SG": "SG",
        # 'SUBN': '',
        "TG": "TG",
        "TGW": "TGW",
        "VPGW": "VPNGW",
    }

    return f"../icons/{MAP[service]}.png" if service in MAP else None


def node(svc: ServiceInstance):
    attrs = {
        "label": svc.label,
        # "style": "filled",
        # "color": graphviz_color(self.service_name)
    }

    image = graphviz_icon(svc.service_name, svc.type_info)
    if image:
        attrs["image"] = image

    s_attrs = " ".join([f'{k}="{v}"' for k, v in attrs.items()])
    return f"{graphviz_id(svc.id)} [{s_attrs}]"


def edge(source: ServiceInstance, target: ServiceInstance, vpc: VPC):
    if source.service_name == "SUBN":
        return None

    # if source.service_name == 'EC2' and target.service_name == 'SG':
    #     return None

    attrs = {
        # "style": "filled",
        "color": graphviz_color(source.service_name)
    }

    # Handle Backwards links
    if level(source.service_name) > level(target.service_name):
        attrs["constraint"] = False
        attrs["style"] = "invis"
    elif source.cost_per_month > 0 and target.service_name == "SG":
        attrs["constraint"] = False
        attrs["style"] = "invis"

    # Route table
    if source.service_name == "ENI" and target.service_name == "RTB":
        rel = Relation(source.id, target.id)
        if rel not in vpc.relations:
            attrs["weight"] = 10
            attrs["style"] = "invis"
    elif target.service_name == "RTB":
        attrs["color"] = "mediumpurple"

    s_attrs = " ".join([f'{k}="{v}"' for k, v in attrs.items()])
    return f"{ graphviz_id(source.id) } -> { graphviz_id(target.id) } [{s_attrs}]"


# -----------------------------------------------------------------------------
# Main methods


def render(full_path: str):
    gv_dir, file_name = os.path.split(full_path)
    cmd = f"cd {gv_dir} && dot -Tpng -x -O {file_name}"
    os.system(cmd)


def to_graphviz(vpc: VPC, stream, s3_buckets=None):
    # Add all edge nodes to the connected set
    connected = set(
        [x.source for x in vpc.relations] + [x.target for x in vpc.relations]
    )

    # Ensure all Storage & Compute services are included
    for v in vpc.services:
        if v.service_name in STORAGE or v.service_name in COMPUTE:
            connected.add(v.id)
        if v.service_name == "HZ":
            connected.add(v.id)

    # Find if there are single AZ or single subnet services
    az_tally = Counter()
    sn_tally = Counter()
    for availzone in vpc.availability_zones:
        az_tally.update(availzone.service_ids)
        for k in availzone.subnet_ids:
            sn_tally.update(vpc.subnets[k])

    single_subnet = set([k for k, v in sn_tally.items() if v == 1])
    single_az = set(
        [k for k, v in az_tally.items() if v == 1 and k not in single_subnet]
    )

    # Accidently includes services in several SN and AZs
    # for k, v in sn_tally.items():
    #     if v > 1 and k not in az_tally:
    #         single_az.add(k)

    contained = single_subnet.union(single_az)

    start_vpc = level("VPC")
    end_top = level("SUBN")
    end_subnet = level("ENI")

    route53_services: List[ServiceInstance] = []
    top_services: List[ServiceInstance] = []
    bottom_services: List[ServiceInstance] = []
    ingress_services: List[ServiceInstance] = []
    policy_services: List[ServiceInstance] = []
    # S3 is account/region-level, not part of vpc.services; pinned to the
    # bottom of every VPC diagram from the shared context list.
    s3_services: List[ServiceInstance] = [node(b) for b in (s3_buckets or [])]
    ranks: Dict[str, list] = defaultdict(list)
    enis: List[NetworkInterface] = []
    rtbs: List[ServiceInstance] = []
    # Synthetic Internet/on-prem -> door edges, and whether to draw the source.
    ingress_edges: List[str] = []
    has_internet = False

    # Route the services to the appropriate area
    for v in vpc.services:
        l = level(v.service_name)
        nv = node(v)
        display_outside_sn = v.id in connected and v.id not in contained

        if v.service_name in INGRESS_SVC:
            # The doors ALWAYS render — showing the door exists is the point,
            # even when nothing else in the scan references it.
            ingress_services.append(nv)
            if v.service_name in INET_DOORS:
                has_internet = True
                ingress_edges.append(f'INET -> {graphviz_id(v.id)} [color="grey60"]')
            elif v.service_name in ONPREM_DOORS:
                has_internet = True
                ingress_edges.append(
                    f'INET -> {graphviz_id(v.id)} [color="grey60" style=dashed]'
                )
        elif l < start_vpc:
            route53_services.append(nv)
        elif l < end_top and v.id not in contained:
            top_services.append(nv)
        elif v.service_name in BOTTOM_SVC and display_outside_sn:
            bottom_services.append(nv)
        elif v.service_name in POLICY_SVC and display_outside_sn:
            policy_services.append(nv)
        # else:
        #     print(
        #         f'Unrouted {v}\t{l}, {nv}, {display_outside_sn}',
        #         v.id in connected,
        #         v.id in contained,
        #         v.id in single_subnet,
        #         v.id in single_az
        #     )

        if v.service_name not in ["SUBN"] and v.id in connected:
            ranks[v.service_name].append(v.id)

        if v.service_name == "ENI" and v.id in connected:
            enis.append(v.id)
        if v.service_name == "RTB" and v.id in connected:
            rtbs.append(v.id)

    azs = []
    az_i = 0
    sn_i = 0
    for azk, availzone in sorted(vpc.azs.items()):
        # Create the holding object
        az = GVAZ(str(100 + az_i * 10), azk)
        az_i += 1
        azs.append(az)

        az.top_services: List[ServiceInstance] = []
        az.bottom_services: List[ServiceInstance] = []

        # Route the services to the appropriate area
        for v in vpc.services:
            l = level(v.service_name)
            nv = node(v)
            display_in_az = v.id in availzone.service_ids and v.id in single_az

            if l < end_top and display_in_az and v.service_name not in INGRESS_SVC:
                az.top_services.append(nv)
            if l > end_subnet and display_in_az:
                az.bottom_services.append(nv)

        subnet_instances: List[Subnet] = [vpc[k] for k in availzone.subnet_ids]

        for subnet in sorted(subnet_instances, key=cidr_sort):
            label = f"{subnet.subnet_type}\\n{subnet.cidr}\\n{subnet.name}\\n{subnet.instance_name}"
            sn = GVSN(str(1000 + sn_i * 10), subnet.id, label)
            sn_i += 1
            az.subnets.append(sn)

            inside = [
                vpc[x]
                for x in vpc.subnets[subnet.id]
                if x in connected and x in single_subnet
            ]
            for x in sorted(inside, key=lambda x: (x.service_name, x.instance_name)):
                sn.services.append(node(x))

    # Output edges
    raw = [
        edge(vpc[x.source], vpc[x.target], vpc)
        for x in sorted(set(vpc.relations))
        if x.source in vpc and x.target in vpc
    ]

    # Add ENI to RTB edges
    for eni, rtb in product(enis, rtbs):
        raw.append(edge(vpc[eni], vpc[rtb], vpc))

    edges = sorted([x for x in raw if x is not None])

    # Set up the Jinja environment
    JinjaEnv = Environment(loader=DictLoader({"template": TEMPLATE}))
    JinjaEnv.filters.update({"graphviz_id": graphviz_id})

    gv_out = JinjaEnv.get_template("template").render(
        {
            "vpc_name": f"{vpc.name}\\n{vpc.id}\\n{vpc.cidr_block}",
            "route53_services": route53_services,
            "top_services": top_services,
            "azs": azs,
            "bottom_services": bottom_services,
            "ingress_services": ingress_services,
            "policy_services": policy_services,
            "s3_services": s3_services,
            "svc_types": ranks,
            "ingress_edges": ingress_edges,
            "edges": edges,
            "has_internet": has_internet,
            "inet_icon": graphviz_icon("INET"),
        }
    )

    stream.write(gv_out)
