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

TOP_SVC = ["ASG", "ELBv1", "ELBv2", "EPT-GWLB", "VPGW", "TG", "EKS", "Lambda", "EPT-I"]
SUBNET_SVC = ["EC2", "ENI", "NAT"]
BOTTOM_SVC = ["RDS"]
NW_SVC = ["ACL", "EPT-GW", "IGW", "RTB", "PEER", "SG"]

LEVELS = [
    ["R53"],
    ["HZ"],
    ["VPC"],
    ["VPGW"],
    ["EPT-GWLB"],
    ["ACL"],
    ["ASG"],
    ["ELBv1", "ELBv2"],
    ["TG"],
    ["EKS"],
    ["Lambda"],
    ["EPT-I"],
    ["SUBN"],
    ["EC2", "NAT"],
    ["ENI"],
    ["RDS"],
    ["RTB"],
    ["SG"],
    ["EPT-GW", "PEER", "IGW"],
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
    "VPGW",
]
STORAGE = ["EPT-GW", "RDS"]
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

    subgraph cluster_10 {
        label="{{ vpc_name }}"

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

        subgraph cluster_93 {
            style="invis"
            {% for svc in nw_services -%}
            {{ svc }}
            {% endfor %}
        }
    }

    {% for svcs in svc_types.values() -%}
    // { rank=same; {% for svc in svcs %}{{ svc | graphviz_id }}; {% endfor %} }
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
        "ASG": "ASG",
        "EC2": "EC2",
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
        "SG": "SG",
        # 'SUBN': '',
        "TG": "TG",
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
        attrs["color"]: "mediumpurple"

    s_attrs = " ".join([f'{k}="{v}"' for k, v in attrs.items()])
    return f"{ graphviz_id(source.id) } -> { graphviz_id(target.id) } [{s_attrs}]"


# -----------------------------------------------------------------------------
# Main methods


def render(full_path: str):
    gv_dir, file_name = os.path.split(full_path)
    cmd = f"cd {gv_dir} && dot -Tpng -x -O {file_name}"
    os.system(cmd)


def to_graphviz(vpc: VPC, stream):
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
    nw_services: List[ServiceInstance] = []
    ranks: Dict[str, list] = defaultdict(list)
    enis: List[NetworkInterface] = []
    rtbs: List[ServiceInstance] = []

    # Route the services to the appropriate area
    for v in vpc.services:
        l = level(v.service_name)
        nv = node(v)
        display_outside_sn = v.id in connected and v.id not in contained

        if l < start_vpc:
            route53_services.append(nv)
        elif l < end_top and v.id not in contained:
            top_services.append(nv)
        elif v.service_name in BOTTOM_SVC and display_outside_sn:
            bottom_services.append(nv)
        elif v.service_name in NW_SVC and display_outside_sn:
            nw_services.append(nv)
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

            if l < end_top and display_in_az:
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
            "nw_services": nw_services,
            "svc_types": ranks,
            "edges": edges,
        }
    )

    stream.write(gv_out)
