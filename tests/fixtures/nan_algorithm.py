def step(context):
    return {str(f["id"]): {"position": [float("nan"), 0, -3]} for f in context["followers"]}
