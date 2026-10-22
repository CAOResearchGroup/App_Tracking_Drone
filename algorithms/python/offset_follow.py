"""Trusted user plugin. Input/output: metres, seconds, radians, COMMON NED.

Return exactly one target per follower; choose position OR velocity per target.
No communication code belongs here; Python Multi-Drone routes the targets.
"""


def step(context):
    leader = context["leader"]["position"]
    return {
        str(follower["id"]): {
            "position": [p+offset for p, offset in zip(leader, follower["offset"])],
        }
        for follower in context["followers"]
    }
