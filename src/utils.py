def parse_arn(arn):
    # http://docs.aws.amazon.com/general/latest/gr/aws-arns-and-namespaces.html
    elements = arn.split(":")

    result = {
        "arn": elements[0],
        "partition": elements[1],
        "service": elements[2],
        "region": elements[3],
        "account": elements[4],
    }
    if len(elements) == 7:
        result["resourcetype"], result["resource"] = elements[5:]
    elif "/" not in elements[5]:
        result["resource"] = elements[5]
        result["resourcetype"] = None
    else:
        result["resourcetype"], result["resource"] = elements[5].split("/", maxsplit=1)
    return result
