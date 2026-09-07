import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

from serving.app import langfuse


class RequestTraceTests(unittest.TestCase):
    def test_uses_stable_trace_name_separately_from_step_name(self) -> None:
        span = Mock()
        client = Mock()
        client.create_trace_id.return_value = "trace-id"
        client.start_as_current_observation.return_value = nullcontext(span)
        propagate = Mock(return_value=nullcontext())

        with patch.object(langfuse, "_get_client", return_value=client), patch.object(
            langfuse, "_propagate_attributes", propagate
        ):
            with langfuse.request_trace(
                "request-id",
                name="retrieval-step",
                component="retrieval",
                trace_name="kubemind-request",
            ) as active_span:
                self.assertIs(active_span, span)

        client.start_as_current_observation.assert_called_once_with(
            trace_context={"trace_id": "trace-id"},
            name="retrieval-step",
            as_type="agent",
            input=None,
            metadata={"request_id": "request-id", "component": "retrieval"},
        )
        propagate.assert_called_once_with(
            session_id=None,
            metadata={"request_id": "request-id", "component": "retrieval"},
            tags=["retrieval"],
            trace_name="kubemind-request",
        )


if __name__ == "__main__":
    unittest.main()
