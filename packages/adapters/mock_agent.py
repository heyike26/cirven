"""A fixed original model-side script; tools still execute through the real Runner."""


class MockAgent:
    def tool_proposal(self, run):
        import json
        spec = json.loads(run["frozen_inputs"])["spec"]
        behavior = spec["runtime_profile"]["behavior"]
        corrupt = behavior == "persistent_failure" or (behavior == "repairable" and run["attempt_id"] == 1)
        return {"behavior": behavior, "candidate_text": spec["goal"] + ("-original-fault\n" if corrupt else "\n")}

    def messages(self, run):
        yield {"type": "model_text", "runtime": "mock", "text": "Preparing the original fixture candidate"}
        yield {"type": "tool_request", "runtime": "mock", "tool": "trusted-original-python"}
