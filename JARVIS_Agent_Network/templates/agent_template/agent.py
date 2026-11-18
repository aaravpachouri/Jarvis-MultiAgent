"""Agent Template specialist entry point.

The identity is discoverable now. The implementation is intentionally not registered
as executable until its real tool adapters, behavior and verification tests are ready.
"""


class AgentTemplateAgent:
    """Future handler for template for future specialist agents."""

    async def __call__(self, task, context):
        raise NotImplementedError(
            "Agent Template is registered as an agent identity, but its specialist handler "
            "has not been implemented yet. It must not report simulated work as complete."
        )


def create_handler():
    # Returning None keeps a profile-only agent out of the executable pool.
    return None
